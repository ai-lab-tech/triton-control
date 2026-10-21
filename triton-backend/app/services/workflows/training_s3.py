"""Resolve an owned S3 profile for training-only workflow submissions."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.exceptions import BadRequestError, NotFoundError
from app.repositories import s3_profiles, workflow_s3_credentials

_PROFILE_NAME = "triton-control.ai/mlflow-s3-profile-name"
_PROFILE_ID = "triton-control.ai/mlflow-s3-profile-id"
_CA_VOLUME = "triton-control-training-s3-ca"
_CA_PATH = "/var/run/triton-control/training-s3"
_ENV_NAMES = {
    "S3_ENDPOINT_URL",
    "S3_BUCKET",
    "S3_ADDRESSING_STYLE",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_CA_BUNDLE",
    "TRITON_CONTROL_S3_PROFILE_ID",
}


def prepare_training_s3(workflow: dict[str, Any], session: Session, owner_id: int, namespace: str) -> None:
    """Inject repository, connection settings, and Secret references, never credentials."""
    annotations = (workflow.get("metadata") or {}).get("annotations") or {}
    if annotations.get("triton-control.ai/mlflow-deploy-template"):
        return  # Preserve the existing deployment delegation contract.
    name = annotations.get(_PROFILE_NAME, "").strip()
    profile_id = annotations.get(_PROFILE_ID, "")
    if not name and not profile_id:
        return
    if bool(name) == bool(profile_id):
        raise BadRequestError("Specify exactly one MLflow S3 profile name or ID")
    if name:
        profile = s3_profiles.find_by_name_for_owner(session, owner_id, name)
    else:
        try:
            numeric_id = int(profile_id)
        except (TypeError, ValueError) as exc:
            raise BadRequestError("A valid MLflow S3 profile ID is required") from exc
        if numeric_id <= 0:
            raise BadRequestError("A valid MLflow S3 profile ID is required")
        profile = s3_profiles.find_for_owner(session, owner_id, numeric_id)
    if profile is None:
        raise NotFoundError("S3 profile not found")
    linked = [
        row
        for row in workflow_s3_credentials.list_for_profile(session, profile.id or 0)
        if row.namespace == namespace and row.created_by_user_id == owner_id
    ]
    if len(linked) != 1:
        raise BadRequestError("Link exactly one repository for this S3 profile under Workflows -> Configure S3 Secrets")
    secret_name = linked[0].secret_name
    spec = workflow.setdefault("spec", {})
    reference = {"configMap": secret_name, "key": "repository"}
    if spec.get("artifactRepositoryRef") not in (None, reference):
        raise BadRequestError("Workflow artifact repository conflicts with the selected S3 profile")
    spec["artifactRepositoryRef"] = reference
    endpoint = profile.endpoint if "://" in profile.endpoint else f"https://{profile.endpoint}"
    use_ca = bool(profile.ca_certificate and endpoint.startswith("https://"))
    volumes = spec.setdefault("volumes", [])
    if use_ca:
        if any(volume.get("name") == _CA_VOLUME for volume in volumes):
            raise BadRequestError("Managed training S3 CA volume is already set")
        volumes.append(
            {
                "name": _CA_VOLUME,
                "secret": {
                    "secretName": secret_name,
                    "items": [{"key": "ca.pem", "path": "ca.pem"}],
                },
            }
        )
    for template in spec.get("templates", []):
        pod = template.get("container") or template.get("script")
        if pod is None:
            continue
        env = pod.setdefault("env", [])
        if any(item.get("name") in _ENV_NAMES for item in env):
            raise BadRequestError("A managed training S3 environment variable is already set")
        env.extend(
            [
                {"name": "S3_ENDPOINT_URL", "value": endpoint},
                {"name": "S3_BUCKET", "value": profile.bucket},
                {"name": "S3_ADDRESSING_STYLE", "value": "path" if profile.force_path_style else "virtual"},
                {"name": "AWS_DEFAULT_REGION", "value": profile.region or "us-east-1"},
                {"name": "TRITON_CONTROL_S3_PROFILE_ID", "value": str(profile.id)},
                {
                    "name": "AWS_ACCESS_KEY_ID",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": secret_name,
                            "key": "access-key-id",
                        }
                    },
                },
                {
                    "name": "AWS_SECRET_ACCESS_KEY",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": secret_name,
                            "key": "secret-access-key",
                        }
                    },
                },
            ]
        )
        if use_ca:
            mounts = pod.setdefault("volumeMounts", [])
            if any(item.get("mountPath", "").rstrip("/") == _CA_PATH for item in mounts):
                raise BadRequestError("Managed training S3 CA mount is already set")
            mounts.append({"name": _CA_VOLUME, "mountPath": _CA_PATH, "readOnly": True})
            env.append({"name": "AWS_CA_BUNDLE", "value": f"{_CA_PATH}/ca.pem"})
