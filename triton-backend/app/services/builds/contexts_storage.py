"""Administrator-owned staging configuration and version-pinned S3 I/O."""

import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator
from urllib.parse import urlparse

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError
from sqlmodel import Session

from app.exceptions import BadRequestError, ForbiddenError, ServiceUnavailableError
from app.repositories import build_config, registry_config, s3_profiles
from app.schemas.build_contexts import ContextManifest
from app.schemas.build_policy import BuilderImage, BuildPolicy, LocalArchivePolicy
from app.services.storage.s3_client import S3Config, build_s3_client


def saved_pull_credentials(session: Session) -> tuple[str, str]:
    row = registry_config.get(session)
    if not row or not row.dockerconfigjson_enc:
        raise ForbiddenError("Save pull credentials first, or uncheck the option to reuse them")
    encrypted = row.dockerconfigjson_enc
    revision = hashlib.sha256(encrypted.encode()).hexdigest()
    return "pull-" + revision, encrypted


def policy(session: Session) -> BuildPolicy:
    if os.getenv("TRITON_CONTROL_MODE", "development") != "development":
        raise ForbiddenError("Build context staging is available only in development")
    base = base_policy(session)
    destination = build_config.get_destination(session)
    if isinstance(base, LocalArchivePolicy):
        if destination and (destination.namespace != base.namespace or destination.repositories):
            raise ForbiddenError("Registry publishing or a different namespace requires a configured registry builder")
        return base
    if destination:
        push_secret = destination.push_secret or base.push_secret
        if destination.reuse_pull_credentials:
            push_secret, _ = saved_pull_credentials(session)
        if push_secret in (base.base_pull_secret, base.runtime_pull_secret):
            raise ForbiddenError("Choose a push secret separate from the builder pull secrets")
        repositories = tuple(destination.repositories)
        if not repositories:
            # Use only cluster Service destinations from the qualified installation policy.
            repositories = tuple(repo for repo in base.allowed_repositories
                                 if repo.split("/", 1)[0].split(":")[0].endswith((".svc", ".svc.cluster.local")))
            if not repositories:
                raise ForbiddenError("Local builds require a cluster registry in the installation build policy")
        return base.model_copy(update={
            "namespace": destination.namespace, "push_secret": push_secret,
            "anonymous_push": not (destination.push_secret or destination.reuse_pull_credentials),
            "allowed_repositories": repositories,
        })
    return base


def default_executor() -> BuilderImage:
    configured = os.getenv("TRITON_BUILD_DEFAULT_EXECUTOR")
    try:
        return BuilderImage.model_validate_json(configured) if configured is not None else BuilderImage()
    except ValidationError:
        raise ServiceUnavailableError("Invalid installation default build executor") from None


def with_default_executor(policy: BuildPolicy) -> BuildPolicy:
    if "executor_image" in policy.model_fields_set:
        return policy
    return policy.model_copy(update=default_executor().model_dump())


def base_policy(session: Session) -> BuildPolicy:
    row = build_config.get(session)
    if row:
        return with_default_executor(BuildPolicy.model_validate(row.policy))
    namespace = os.getenv("TRITON_LOCAL_BUILD_NAMESPACE")
    if namespace and not os.getenv("TRITON_BUILD_POLICY_FILE") and os.getenv("TRITON_CONTROL_MODE", "development") == "development":
        name = os.getenv("TRITON_LOCAL_BUILD_PROFILE")
        profiles = s3_profiles.list_by_name(session, name) if name else s3_profiles.list_all(session)
        if len(profiles) != 1:
            raise ServiceUnavailableError("Select a single staging S3 profile using localBuilds.stagingProfile")
        profile = profiles[0]
        if urlparse(profile.endpoint).scheme != "https":
            raise ServiceUnavailableError("Local build staging requires an HTTPS S3 profile")
        prefix = profile.prefix.strip("/")
        return LocalArchivePolicy(
            **default_executor().model_dump(), namespace=namespace, enabled=True, anonymous_push=True,
            allowed_repositories=("local/serving",), s3_profile_name=profile.name,
            staging_prefix=(prefix + "/" if prefix else "") + "build-contexts/",
            base_pull_secret="unused-base", runtime_pull_secret="unused-runtime", push_secret="unused-push",
            provisioning_revision="local-archive-v1", egress_cidrs=("127.0.0.1/32",),
            timeout_seconds=2400, memory_mib=3072, cpu_millis=2000,
        )
    return deployment_policy()


def deployment_policy() -> BuildPolicy:
    if os.getenv("TRITON_CONTROL_MODE", "development") != "development":
        raise ForbiddenError("Build context staging is available only in development")
    filename = os.getenv("TRITON_BUILD_POLICY_FILE", "")
    if not filename:
        raise ForbiddenError("Build context staging has not been configured")
    try:
        with Path(filename).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Oversized policy")
        return with_default_executor(BuildPolicy.model_validate_json(raw))
    except (OSError, ValueError, ValidationError):
        raise ServiceUnavailableError("Invalid installation build policy") from None


def retention_seconds() -> int:
    try:
        days = int(os.getenv("TRITON_BUILD_CONTEXT_RETENTION_DAYS", "7"))
        if not 1 <= days <= 365:
            raise ValueError("Out of bounds")
        return days * 86400
    except ValueError:
        raise ServiceUnavailableError("Build context retention must be between 1 and 365 days") from None


def binding(session: Session, settings: BuildPolicy) -> dict[str, Any]:
    rows = s3_profiles.list_by_name(session, settings.s3_profile_name)
    if len(rows) != 1:
        raise ServiceUnavailableError("One unambiguous build staging S3 profile is required")
    profile = rows[0]
    prefix = profile.prefix.strip("/")
    if prefix and not settings.staging_prefix.startswith(prefix + "/"):
        raise ForbiddenError("Build staging prefix is outside the configured profile")
    return {"profile_id": profile.id, "endpoint": profile.endpoint, "bucket": profile.bucket,
            "prefix": settings.staging_prefix}


class ContextStore:
    def __init__(self, client: Any, bucket: str, ca_certificate: str = "") -> None:
        self.client, self.bucket, self.ca_certificate = client, bucket, ca_certificate

    def put(self, key: str, stream: BinaryIO, manifest: ContextManifest) -> str:
        try:
            if self.client.get_bucket_versioning(Bucket=self.bucket).get("Status") != "Enabled":
                raise BadRequestError("Build staging bucket versioning must be enabled")
            result = self.client.put_object(
                Bucket=self.bucket, Key=key, Body=stream, ContentLength=manifest.archive_bytes,
                ContentType="application/gzip",
            )
            version = result.get("VersionId")
            if not version or version == "null":
                raise BadRequestError("Build staging did not return an immutable object version")
            return str(version)
        except (BotoCoreError, ClientError):
            raise ServiceUnavailableError("Build context storage unavailable") from None

    def download_url(self, key: str, version: str, seconds: int) -> str:
        """Short-lived access to exactly one immutable version; never persist in evidence."""
        url = str(self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key, "VersionId": version},
            ExpiresIn=seconds,
        ))
        if urlparse(url).scheme != "https":
            raise ForbiddenError("Executor context downloads require trusted HTTPS")
        return url

    def verify(
        self, key: str, version: str, manifest: ContextManifest, checkpoint: Callable[[], None] = lambda: None,
    ) -> None:
        body = None
        started = time.monotonic()
        last_check = 0.0
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version)
            body = result["Body"]
            if result.get("VersionId") != version or result["ContentLength"] != manifest.archive_bytes:
                raise BadRequestError("Retained build context identity differs")
            size = 0
            hasher = hashlib.sha256()
            while chunk := body.read(65536):
                if time.monotonic() - started > 120:
                    raise ServiceUnavailableError("Build context verification deadline exceeded")
                if time.monotonic() - last_check >= 1:
                    checkpoint()
                    last_check = time.monotonic()
                size += len(chunk)
                if size > manifest.archive_bytes:
                    raise BadRequestError("Retained build context exceeds recorded size")
                hasher.update(chunk)
            if size != manifest.archive_bytes or "sha256:" + hasher.hexdigest() != manifest.archive_digest:
                raise BadRequestError("Retained build context checksum differs")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "NoSuchVersion", "404", "NotFound"}:
                raise BadRequestError("Retained build context is missing") from None
            raise ServiceUnavailableError("Retained build context unavailable") from None
        except BotoCoreError:
            raise ServiceUnavailableError("Retained build context unavailable") from None
        finally:
            if body is not None:
                body.close()

    def recover(self, key: str, manifest: ContextManifest, checkpoint: Callable[[], None] = lambda: None) -> str | None:
        """Observe an uncertain PUT by its already-persisted unique attempt key."""
        try:
            result = self.client.list_object_versions(Bucket=self.bucket, Prefix=key, MaxKeys=10)
            if result.get("IsTruncated"):
                raise BadRequestError("Unexpected build context version count")
            for obj in result.get("Versions", []):
                if obj["Key"] == key and obj["VersionId"] != "null":
                    self.verify(key, obj["VersionId"], manifest, checkpoint)
                    return str(obj["VersionId"])
            return None
        except (BotoCoreError, ClientError):
            raise ServiceUnavailableError("Build context recovery unavailable") from None

    def delete_prefix(self, prefix: str, checkpoint: Callable[[], None] = lambda: None) -> None:
        # Include noncurrent versions and delete markers; deleting only the current
        # key would retain the bytes indefinitely in a versioned bucket.
        try:
            paginator = self.client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, PaginationConfig={"PageSize": 100}):
                checkpoint()
                for obj in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
                    checkpoint()
                    self.client.delete_object(Bucket=self.bucket, Key=obj["Key"], VersionId=obj["VersionId"])
        except (BotoCoreError, ClientError):
            raise ServiceUnavailableError("Build context cleanup unavailable") from None


@contextmanager
def storage(session: Session, expected: dict[str, Any]) -> Iterator[ContextStore]:
    profile = s3_profiles.get(session, expected["profile_id"])
    if profile is None or profile.endpoint != expected["endpoint"] or profile.bucket != expected["bucket"]:
        raise ForbiddenError("Build storage binding changed; restore the original binding before recovery")
    prefix = profile.prefix.strip("/")
    if prefix and not expected["prefix"].startswith(prefix + "/"):
        raise ForbiddenError("Build storage scope changed")
    config = S3Config(
        s3_endpoint=profile.endpoint, s3_bucket=profile.bucket, s3_access_key=profile.access_key,
        s3_secret_key_enc=profile.secret_key_enc, s3_use_https=urlparse(profile.endpoint).scheme == "https",
        s3_verify_ssl=True, s3_ca_certificate=profile.ca_certificate, s3_region=profile.region,
        s3_address_style="path" if profile.force_path_style else "virtual",
    )
    client = build_s3_client(config, client_config=Config(
        connect_timeout=3, read_timeout=10, retries={"max_attempts": 1, "mode": "standard"},
    ))
    try:
        yield ContextStore(client, profile.bucket, profile.ca_certificate or "")
    finally:
        client.close()
