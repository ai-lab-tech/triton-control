"""Local authorization/admission and non-HTTP credential resolver for S08-B.

Admission results are diagnostics, not approval or durable execution authority.
Consumers must resolve again immediately before each external operation.
"""

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, TypeAdapter, ValidationError
from sqlmodel import Session

from app.core import installation_policy
from app.core.crypto import decrypt_secret
from app.core.identity import require_user_entity
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.repositories import local_bindings as repository
from app.repositories import s3_profiles
from app.schemas.local_bindings import (
    AdmissionRequest,
    AdmissionResult,
    BindingPolicy,
    CredentialBinding,
    CredentialInput,
    CredentialResponse,
    GitCredential,
    PolicyResponse,
    PolicyUpdate,
    RegistryCredential,
    S3Binding,
)
from app.schemas.serving_metadata import GitLocation, MetadataSource, S3Location, cpu_value, memory_value
from app.services.lineage import digest
from app.services.registry_settings import decrypt_registry_secret, encrypt_registry_secret

Purpose = Literal["source-read", "model-storage", "registry-pull", "build-push"]
CREDENTIAL: TypeAdapter[GitCredential | RegistryCredential] = TypeAdapter(CredentialInput)


@dataclass(frozen=True)
class ResolvedCredential:
    """In-process only. Never serialize into responses, snapshots, Jobs or logs."""

    provider: str
    scope: dict[str, Any] = field(repr=False)
    username: SecretStr = field(repr=False)
    password: SecretStr = field(repr=False)


def _actor(session: Session, claims: dict[str, Any], *, admin: bool = False) -> tuple[int, str]:
    if claims.get("token_use"):
        raise ForbiddenError("Interactive user credentials required")
    user = require_user_entity(session, claims)
    session.refresh(user)
    role = user.role.strip().lower()
    if not user.is_active or role not in ({"admin"} if admin else {"admin", "member"}):
        raise ForbiddenError("Local binding access denied")
    return user.id or 0, role


def _mode() -> str:
    mode = os.getenv("TRITON_CONTROL_MODE", "development").strip().lower()
    if mode not in {"development", "production"}:
        raise ForbiddenError("Unsupported installation mode")
    return mode


def get_policy(session: Session, claims: dict[str, Any]) -> PolicyResponse:
    _actor(session, claims, admin=True)
    row = repository.policy(session)
    return PolicyResponse(
        revision=row.revision if row else 0,
        policy=BindingPolicy.model_validate(row.payload) if row else BindingPolicy(),
    )


def _load(session: Session) -> tuple[int, BindingPolicy]:
    row = repository.policy(session)
    if not row:
        raise ForbiddenError("Local binding policy is not configured")
    try:
        return row.revision, BindingPolicy.model_validate(row.payload)
    except ValidationError:
        raise BadRequestError("Stored local binding policy is invalid") from None


def _s3(session: Session, binding: S3Binding) -> ResolvedCredential:
    row = s3_profiles.get(session, binding.profile_id)
    if row:
        session.refresh(row)
    if not row or (row.endpoint.rstrip("/"), row.bucket, row.prefix.strip("/")) != (
        binding.endpoint.rstrip("/"),
        binding.bucket,
        binding.prefix,
    ):
        raise ForbiddenError("S3 binding is unavailable or its configured scope changed")
    endpoint = urlsplit(row.endpoint)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password:
        raise ForbiddenError("S3 endpoint contains an unsupported credential or scheme")
    if not row.access_key or not row.secret_key_enc:
        raise ForbiddenError("S3 binding has no usable credentials")
    return ResolvedCredential(
        "s3",
        {
            "profile_id": row.id,
            "endpoint": row.endpoint,
            "bucket": row.bucket,
            "prefix": binding.prefix,
            "region": row.region,
            "force_path_style": row.force_path_style,
            "ca_certificate": row.ca_certificate,
        },
        SecretStr(row.access_key),
        SecretStr(decrypt_secret(row.secret_key_enc)),
    )


def _credential(session: Session, name: str) -> GitCredential | RegistryCredential:
    row = repository.credential(session, name)
    if not row:
        raise ForbiddenError("Local credential is unavailable")
    try:
        return CREDENTIAL.validate_python(
            {**row.scope, "provider": row.provider, "password": decrypt_registry_secret(row.secret_enc)}
        )
    except (BadRequestError, ValidationError):
        raise ForbiddenError("Local credential is unavailable") from None


def _resolve(session: Session, binding: S3Binding | CredentialBinding, purpose: Purpose) -> ResolvedCredential:
    if isinstance(binding, S3Binding):
        if purpose not in {"source-read", "model-storage"}:
            raise ForbiddenError("Credential purpose mismatch")
        return _s3(session, binding)
    credential = _credential(session, binding.credential)
    if credential.provider != binding.provider:
        raise ForbiddenError("Credential provider mismatch")
    if isinstance(credential, GitCredential):
        if purpose != "source-read":
            raise ForbiddenError("Git credentials are read-only")
    elif credential.permission != {"registry-pull": "pull", "build-push": "push"}.get(purpose):
        raise ForbiddenError("Registry credential purpose mismatch")
    scope = credential.model_dump(mode="json", exclude={"username", "password"})
    return ResolvedCredential(credential.provider, scope, SecretStr(credential.username), credential.password)


def update_policy(session: Session, claims: dict[str, Any], request: PolicyUpdate) -> PolicyResponse:
    installation_policy.require("trust_admin")
    _actor(session, claims, admin=True)
    _mode()
    groups: tuple[tuple[Purpose, Iterable[S3Binding | CredentialBinding]], ...] = (
        ("source-read", request.policy.source_read.values()),
        ("model-storage", request.policy.model_storage.values()),
        ("registry-pull", request.policy.registry_pull.values()),
        ("build-push", request.policy.build_push.values()),
    )
    for purpose, bindings in groups:
        for binding in bindings:
            _resolve(session, binding, purpose)
    revision = repository.save_policy(session, request.expected_revision, request.policy.model_dump(mode="json"))
    return PolicyResponse(revision=revision, policy=request.policy)


def put_credential(
    session: Session,
    claims: dict[str, Any],
    name: str,
    request: GitCredential | RegistryCredential,
) -> CredentialResponse:
    _actor(session, claims, admin=True)
    _mode()
    # The repository checks scope under its write lock, including concurrent recreation.
    scope = request.model_dump(mode="json", exclude={"password"})
    encrypted = encrypt_registry_secret(request.password.get_secret_value())
    if (installation_policy.mode() == "production" and isinstance(request, RegistryCredential)
            and request.permission != "pull"):
        raise ForbiddenError("Production cannot provision build push credentials")
    repository.save_credential(session, name, request.provider, scope, encrypted,
                               require_existing=installation_policy.mode() == "production")
    return CredentialResponse(name=name, provider=request.provider, configured=True)


def get_credential(session: Session, claims: dict[str, Any], name: str) -> CredentialResponse:
    _actor(session, claims, admin=True)
    row = repository.credential(session, name)
    if not row:
        raise NotFoundError("Local credential not found")
    provider: Literal["git", "registry"] = "git" if row.provider == "git" else "registry"
    return CredentialResponse(name=name, provider=provider, configured=bool(row.secret_enc))


def delete_credential(session: Session, claims: dict[str, Any], name: str) -> None:
    _actor(session, claims, admin=True)
    repository.delete_credential(session, name)


def _within(key: str, prefix: str) -> bool:
    return not prefix or key == prefix or key.startswith(prefix + "/")


def _source_location(credential: ResolvedCredential, location: GitLocation | S3Location) -> None:
    if isinstance(location, S3Location):
        if (
            credential.provider != "s3"
            or location.bucket != credential.scope["bucket"]
            or not _within(
                location.key,
                credential.scope["prefix"],
            )
        ):
            raise ForbiddenError("Source is outside the local storage binding")
    elif credential.provider != "git" or location.repository != credential.scope["repository"]:
        raise ForbiddenError("Git repository is outside the read-only binding")


def _registry_scope(credential: ResolvedCredential, image: str) -> None:
    host, _, repository_path = image.partition("/")
    repository_path = repository_path.split("@", 1)[0]
    if (
        credential.provider != "registry"
        or host != credential.scope["registry"]
        or not _within(
            repository_path,
            credential.scope["repository_prefix"],
        )
    ):
        raise ForbiddenError("Image repository is outside the local registry binding")


def _admit(
    session: Session,
    claims: dict[str, Any],
    request: AdmissionRequest,
) -> tuple[AdmissionResult, dict[str, ResolvedCredential]]:
    actor, role = _actor(session, claims)
    _mode()
    revision, policy = _load(session)
    source, target, release = request.source, request.target, request.release
    source_id, target_id = source.metadata.name, target.metadata.name
    source_grant, target_grant = policy.sources.get(source_id), policy.targets.get(target_id)
    if not source_grant or not target_grant or source_id not in target_grant.source_ids:
        raise ForbiddenError("Source/target association is not locally authorized")
    if role != "admin" and (actor not in source_grant.user_ids or actor not in target_grant.user_ids):
        raise ForbiddenError("Source/target access denied")
    if source.spec.location != source_grant.location or source.spec.read_credential != source_grant.read_binding:
        raise ForbiddenError("Source differs from its administrator-managed scope")
    if target_id not in source.spec.allowed_targets or any(
        name not in policy.targets or source_id not in policy.targets[name].source_ids
        for name in source.spec.allowed_targets
    ):
        raise ForbiddenError("Source declares targets outside its local allowlist")
    selected = target.spec.credentials
    if (
        selected.model_storage not in target_grant.model_storage
        or selected.image_registry not in target_grant.registry_pull
    ):
        raise ForbiddenError("Target credential reference is not allowed")
    hardware = policy.hardware.get(request.hardware)
    if hardware is None or request.hardware not in target_grant.hardware:
        raise ForbiddenError("Target hardware is not supported")
    resources = release.spec.resources_per_replica
    peak = target.spec.replicas + target.spec.rollout.max_surge
    if target.spec.replicas > target_grant.max_replicas or resources.gpu_count > hardware.max_gpu_per_replica:
        raise ForbiddenError("Replica or hardware GPU limit exceeded")
    for cap, multiplier in ((target_grant.per_replica, 1), (target_grant.total, peak)):
        if (
            cpu_value(resources.limits.cpu) * multiplier > cpu_value(cap.cpu)
            or memory_value(resources.limits.memory) * multiplier > memory_value(cap.memory)
            or resources.gpu_count * multiplier > cap.gpu
        ):
            raise ForbiddenError("Target capacity limit exceeded (including rollout surge)")
    release_digest = digest(release.model_dump(mode="json", by_alias=True))
    if release_digest != target.spec.release.digest:
        raise BadRequestError("Target selects a different release digest")
    credentials = {
        "source-read": _resolve(session, policy.source_read[source_grant.read_binding], "source-read"),
        "model-storage": _resolve(session, policy.model_storage[selected.model_storage], "model-storage"),
        "registry-pull": _resolve(session, policy.registry_pull[selected.image_registry], "registry-pull"),
    }
    _source_location(credentials["source-read"], source.spec.location)
    models = urlsplit(release.spec.repository.uri)
    model_scope = credentials["model-storage"].scope
    # S3 only initially: HTTPS repositories require a future scoped provider.
    if (
        models.scheme != "s3"
        or models.netloc != model_scope["bucket"]
        or not _within(
            models.path.lstrip("/"),
            model_scope["prefix"],
        )
    ):
        raise ForbiddenError("Model repository is outside the local storage binding")
    _registry_scope(credentials["registry-pull"], release.spec.image)
    if release.spec.repository.materializer_image:
        _registry_scope(credentials["registry-pull"], release.spec.repository.materializer_image)
    result = AdmissionResult(
        policy_revision=revision,
        policy_digest=digest(policy.model_dump(mode="json")),
        source_id=source_id,
        target_id=target_id,
        release_digest=release_digest,
        hardware=hardware,
        peak_replicas=peak,
        bindings={
            "source-read": source_grant.read_binding,
            "model-storage": selected.model_storage,
            "registry-pull": selected.image_registry,
        },
    )
    return result, credentials


def admit(session: Session, claims: dict[str, Any], request: AdmissionRequest) -> AdmissionResult:
    return _admit(session, claims, request)[0]


def resolve_for_execution(
    session: Session,
    claims: dict[str, Any],
    request: AdmissionRequest,
) -> dict[str, ResolvedCredential]:
    """Internal consumers only. Rechecks current actor, policy and credential scope."""
    return _admit(session, claims, request)[1]


def resolve_build_push(
    session: Session,
    claims: dict[str, Any],
    binding_name: str,
    image_repository: str,
) -> ResolvedCredential:
    actor, role = _actor(session, claims)
    if _mode() != "development":
        raise ForbiddenError("Build push is restricted to development installations")
    _, policy = _load(session)
    if role != "admin" and actor not in policy.build_user_ids:
        raise ForbiddenError("Build push access denied")
    binding = policy.build_push.get(binding_name)
    if binding is None:
        raise ForbiddenError("Build push reference is not allowlisted")
    result = _resolve(session, binding, "build-push")
    # A repository, not a URI/tag/Secret path, is expected here.
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.:-]*(/[a-z0-9]+(?:[._-][a-z0-9]+)*)+", image_repository)
        or any(part in {"", ".", ".."} for part in image_repository.split("/"))
    ):
        raise BadRequestError("Expected a registry/repository without tag or digest")
    _registry_scope(result, image_repository)
    return result


def resolve_source_read(
    session: Session,
    claims: dict[str, Any],
    source: MetadataSource,
) -> ResolvedCredential:
    """Authorize a configured source before fetching its target/release documents."""
    actor, role = _actor(session, claims)
    _mode()
    _, policy = _load(session)
    grant = policy.sources.get(source.metadata.name)
    if grant is None or (role != "admin" and actor not in grant.user_ids):
        raise ForbiddenError("Source access denied")
    if source.spec.location != grant.location or source.spec.read_credential != grant.read_binding:
        raise ForbiddenError("Source differs from its administrator-managed scope")
    if any(
        name not in policy.targets or source.metadata.name not in policy.targets[name].source_ids
        for name in source.spec.allowed_targets
    ):
        raise ForbiddenError("Source declares targets outside its local allowlist")
    result = _resolve(session, policy.source_read[grant.read_binding], "source-read")
    _source_location(result, source.spec.location)
    return result
