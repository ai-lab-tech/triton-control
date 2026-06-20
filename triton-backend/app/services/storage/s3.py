"""Business logic for S3-backed Triton model repository access.

Provides use cases that expose S3 model-repository functionality through
the backend API:
  ``get_instance_s3(session, claims, id)``              — read the S3
    configuration of an instance.
  ``update_instance_s3(session, claims, id, request)``  — persist new S3
    credentials, hashing the secret key before storage.
  ``list_instance_s3(session, claims, id, prefix)``     — list objects/
    pseudo-folders under a given prefix in the configured bucket.
  ``get_instance_s3_content(session, claims, id, path)``— download a single
    S3 object as text.
  ``put_instance_s3_content(session, claims,
                             id, path, content)``        — upload / overwrite
    a single S3 object; validates ``config.pbtxt`` files before writing.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError
from sqlmodel import Session

from app.core.access_control import require_member_or_admin
from app.core.crypto import encrypt_secret, hash_secret, is_secret_set
from app.exceptions import BadGatewayError, BadRequestError, NotFoundError, UnsupportedMediaTypeError
from app.mappers import s3_entity_to_dto
from app.repositories import instances
from app.schemas import (
    InstanceS3ConfigDTO,
    S3DeleteResponse,
    S3EntryDTO,
    S3FileContentResponse,
    S3FileWriteResponse,
    S3ListResponse,
    UpdateInstanceS3Request,
)
from app.services.access import get_instance_or_404
from app.services.storage.s3_client import build_s3_client, format_s3_error
from app.services.triton.config import extract_triton_version, validate_triton_config_pbtxt

DEFAULT_S3_REGION = "us-east-1"


def require_s3_client(entity: Any) -> Any:
    """Validate S3 config and return a boto3 client, raising BadRequestError if misconfigured."""
    if not entity.s3_endpoint or not entity.s3_bucket:
        raise BadRequestError("S3 endpoint and bucket are required")
    if not entity.s3_access_key or not entity.s3_secret_key_enc:
        raise BadRequestError("S3 credentials are not configured")
    try:
        return build_s3_client(entity)
    except RuntimeError as exc:
        raise BadRequestError(str(exc)) from exc


def get_instance_s3(session: Session, claims: dict[str, Any], instance_id: int) -> InstanceS3ConfigDTO:
    instance = get_instance_or_404(session, instance_id, claims)
    return s3_entity_to_dto(instance)


def update_instance_s3(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    request: UpdateInstanceS3Request,
) -> InstanceS3ConfigDTO:
    require_member_or_admin(claims)
    instance = get_instance_or_404(session, instance_id, claims)

    instance.s3_enabled = request.enabled

    if not request.enabled:
        instance.s3_endpoint = None
        instance.s3_bucket = None
        instance.s3_region = None
        instance.s3_prefix = None
        instance.s3_access_key = None
        instance.s3_secret_key_hash = None
        instance.s3_secret_key_enc = None
        instance.s3_use_https = False
        instance.s3_verify_ssl = False
        instance.s3_ca_certificate = ""
        instance.s3_address_style = "path"
    else:
        endpoint = _clean_optional(request.endpoint) or instance.s3_endpoint
        bucket = _clean_optional(request.bucket) or instance.s3_bucket
        access_key = _clean_optional(request.access_key) or instance.s3_access_key
        secret_key = _clean_optional(request.secret_key)

        if not endpoint:
            raise BadRequestError("S3 endpoint is required")
        if not bucket:
            raise BadRequestError("S3 bucket is required")
        if not access_key:
            raise BadRequestError("S3 access key is required")
        if not secret_key and not instance.s3_secret_key_enc:
            raise BadRequestError("S3 secret key is required")

        instance.s3_endpoint = _normalize_s3_endpoint(endpoint)
        instance.s3_bucket = bucket
        instance.s3_region = _clean_optional(request.region) or instance.s3_region or DEFAULT_S3_REGION
        instance.s3_prefix = _clean_optional(request.prefix) or instance.s3_prefix
        instance.s3_access_key = access_key
        instance.s3_use_https = _is_https_endpoint(instance.s3_endpoint)
        verify_ssl = bool(request.verify_ssl) if request.verify_ssl is not None else bool(instance.s3_verify_ssl)
        instance.s3_verify_ssl = verify_ssl
        if verify_ssl:
            ca_certificate = request.ca_certificate.strip()
            instance.s3_ca_certificate = ca_certificate or (instance.s3_ca_certificate or "")
        else:
            instance.s3_ca_certificate = ""
        instance.s3_address_style = request.address_style or instance.s3_address_style

        if secret_key is not None and is_secret_set(secret_key):
            instance.s3_secret_key_hash = hash_secret(secret_key)
            instance.s3_secret_key_enc = encrypt_secret(secret_key)

    instances.save(session, instance)
    return s3_entity_to_dto(instance)


def _normalize_s3_endpoint(endpoint: str | None) -> str | None:
    v = (endpoint or "").strip()
    if not v:
        return None
    if not (v.startswith("http://") or v.startswith("https://")):
        v = f"https://{v}"
    return v


def _is_https_endpoint(endpoint: str | None) -> bool:
    return urlparse((endpoint or "").strip()).scheme.lower() == "https"


def _clean_optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def list_instance_s3(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    prefix: str = "",
) -> S3ListResponse:
    instance = get_instance_or_404(session, instance_id, claims)
    if not instance.s3_enabled:
        raise BadRequestError("S3 is not enabled for this instance")

    client = require_s3_client(instance)
    effective_prefix = f"{instance.s3_prefix or ''}{prefix}".lstrip("/")
    try:
        response = client.list_objects_v2(
            Bucket=instance.s3_bucket,
            Prefix=effective_prefix,
            Delimiter="/",
        )
    except (BotoCoreError, ClientError) as exc:
        raise BadGatewayError(
            f"Failed to list S3 objects ({format_s3_error(exc)})",
        ) from exc

    entries: list[S3EntryDTO] = []
    for common in response.get("CommonPrefixes", []) or []:
        folder_prefix = common.get("Prefix", "")
        name = folder_prefix.rstrip("/").split("/")[-1]
        entries.append(
            S3EntryDTO(
                name=name,
                path=f"/{effective_prefix}".rstrip("/") or "/",
                type="folder",
            )
        )

    for item in response.get("Contents", []) or []:
        key = item.get("Key", "")
        if key.endswith("/"):
            continue
        name = key.split("/")[-1]
        entries.append(
            S3EntryDTO(
                name=name,
                path=f"/{effective_prefix}".rstrip("/") or "/",
                type="file",
                size=item.get("Size"),
                modified=item.get("LastModified"),
            )
        )

    return S3ListResponse(prefix=f"/{effective_prefix}".rstrip("/") or "/", entries=entries)


def get_instance_s3_content(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    path: str,
) -> S3FileContentResponse:
    payload, _content_type = get_instance_s3_object_bytes(session, claims, instance_id, path)
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedMediaTypeError("Object is not valid UTF-8 text") from exc

    return S3FileContentResponse(path=f"/{path}", content=content)


def get_instance_s3_object_bytes(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    path: str,
) -> tuple[bytes, str | None]:
    instance = get_instance_or_404(session, instance_id, claims)
    if not instance.s3_enabled:
        raise BadRequestError("S3 is not enabled for this instance")

    client = require_s3_client(instance)
    object_key = f"{instance.s3_prefix or ''}{path}".lstrip("/")

    try:
        response = client.get_object(Bucket=instance.s3_bucket, Key=object_key)
    except ClientError as exc:
        error_code = (exc.response.get("Error") or {}).get("Code")
        if error_code in {"NoSuchKey", "404"}:
            raise NotFoundError("S3 object not found") from exc
        raise BadGatewayError(f"Failed to read S3 object ({format_s3_error(exc)})") from exc
    except BotoCoreError as exc:
        raise BadGatewayError(f"Failed to read S3 object ({format_s3_error(exc)})") from exc

    payload = response.get("Body").read() if response.get("Body") else b""
    content_type = response.get("ContentType")
    return payload, content_type


def put_instance_s3_content(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    path: str,
    content: str | bytes,
    content_type: str | None = None,
) -> S3FileWriteResponse:
    require_member_or_admin(claims)
    instance = get_instance_or_404(session, instance_id, claims)
    if not instance.s3_enabled:
        raise BadRequestError("S3 is not enabled for this instance")

    client = require_s3_client(instance)
    object_key = f"{instance.s3_prefix or ''}{path}".lstrip("/")

    content_bytes = content.encode("utf-8") if isinstance(content, str) else content

    if path.lower().endswith("config.pbtxt"):
        try:
            content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedMediaTypeError("config.pbtxt must be valid UTF-8 text") from exc
        triton_version = extract_triton_version(instance.server_metadata)
        validate_triton_config_pbtxt(content_bytes, triton_version)

    put_args = {
        "Bucket": instance.s3_bucket,
        "Key": object_key,
        "Body": content_bytes,
    }
    if content_type:
        put_args["ContentType"] = content_type

    try:
        client.put_object(**put_args)
    except (BotoCoreError, ClientError) as exc:
        raise BadGatewayError(f"Failed to write S3 object ({format_s3_error(exc)})") from exc

    return S3FileWriteResponse(path=f"/{path}", size=len(content_bytes))


def delete_instance_s3_content(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    path: str,
) -> S3DeleteResponse:
    require_member_or_admin(claims)
    instance = get_instance_or_404(session, instance_id, claims)
    if not instance.s3_enabled:
        raise BadRequestError("S3 is not enabled for this instance")

    client = require_s3_client(instance)
    bucket = instance.s3_bucket
    if not bucket:
        raise BadRequestError("S3 bucket is required")
    object_key = f"{instance.s3_prefix or ''}{path}".lstrip("/")

    try:
        if path.endswith("/"):
            deleted = _delete_s3_prefix(client, bucket, object_key)
        else:
            client.delete_object(Bucket=bucket, Key=object_key)
            deleted = 1
    except (BotoCoreError, ClientError) as exc:
        raise BadGatewayError(f"Failed to delete S3 object ({format_s3_error(exc)})") from exc

    return S3DeleteResponse(path=f"/{path}", deleted=deleted)


def _delete_s3_prefix(client: Any, bucket: str, prefix: str) -> int:
    deleted = 0
    continuation_token: str | None = None

    while True:
        list_args: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            list_args["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**list_args)
        keys = [{"Key": item.get("Key")} for item in response.get("Contents", []) or [] if item.get("Key")]

        for index in range(0, len(keys), 1000):
            chunk = keys[index : index + 1000]
            if not chunk:
                continue
            client.delete_objects(Bucket=bucket, Delete={"Objects": chunk, "Quiet": True})
            deleted += len(chunk)

        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
        if not continuation_token:
            break

    if deleted == 0:
        client.delete_object(Bucket=bucket, Key=prefix)

    return deleted
