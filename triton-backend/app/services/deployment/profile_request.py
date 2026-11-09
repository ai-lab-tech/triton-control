"""Resolve a global S3 profile into the existing deployment request shape."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from sqlmodel import Session

from app.core.crypto import decrypt_secret
from app.core.identity import require_user_entity
from app.exceptions import BadRequestError, NotFoundError
from app.repositories import s3_profiles
from app.schemas import CreateDeploymentRequest

_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def resolve_profile_request(
    request: CreateDeploymentRequest, session: Session, claims: dict[str, Any]
) -> CreateDeploymentRequest:
    if claims.get("token_use") in {"workspace_deployment", "workflow_deployment"} and request.s3_profile_id is None:
        raise BadRequestError("Managed deployment credentials require an S3 profile")
    if claims.get("token_use") == "mlflow_workflow":
        if (
            request.s3_profile_id != claims.get("allowed_s3_profile_id")
            or request.deployment_name != claims.get("allowed_deployment_name")
        ):
            raise BadRequestError("Workflow token is not valid for this deployment or S3 profile")
    if request.s3_profile_id is None:
        raise BadRequestError("Select an admin-configured S3 profile")

    require_user_entity(session, claims)
    profile = s3_profiles.get(session, request.s3_profile_id)
    if profile is None:
        raise NotFoundError("S3 profile not found")

    prefix = (request.repository_prefix or "").strip("/")
    if any(part in {".", ".."} for part in prefix.split("/")):
        raise BadRequestError("Invalid repository prefix")
    if claims.get("token_use") == "mlflow_workflow" and not request.model_uri:
        raise BadRequestError("Workflow deployment requires model_uri")
    if request.model_uri:
        uri = urlsplit(request.model_uri or "")
        parts = uri.path.strip("/").split("/")
        if (
            uri.scheme != "s3"
            or not uri.netloc
            or uri.query
            or uri.fragment
            or len(parts) < 2
            or any(part in {"", ".", ".."} for part in parts)
            or uri.netloc != profile.bucket
        ):
            raise BadRequestError("model_uri must name a model in the selected S3 profile bucket")
        prefix, model_name = "/".join(parts[:-1]), parts[-1]
        if (prefix != request.repository_prefix or model_name != request.model_name
                or not _MODEL_NAME.fullmatch(model_name)):
            raise BadRequestError("model_uri, repository_prefix and model_name do not match")

    endpoint = profile.endpoint.rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise BadRequestError("S3 profile endpoint must be an HTTP(S) URL")
    secret = decrypt_secret(profile.secret_key_enc)
    return request.model_copy(update={
        "s3_url": f"s3://{endpoint}/{profile.bucket}/{prefix}",
        "s3_access_key": profile.access_key,
        "s3_secret_key": secret,
        "s3_region": profile.region,
        "s3_ca_certificate": profile.ca_certificate,
    })
