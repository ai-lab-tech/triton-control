"""Keep user-authored workflows from reading other workloads' credentials."""

from __future__ import annotations

import os
import re
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.exceptions import BadRequestError, ForbiddenError
from app.services.workflows.artifact_repository import REPOSITORY_KEY


def validate_proxy_write(method: str, path: str) -> None:
    """Do not let alternate Argo write APIs bypass managed submission checks."""
    if method.upper() in {"GET", "HEAD", "OPTIONS", "DELETE"}:
        return
    path = path.strip("/")
    if method.upper() == "POST" and re.fullmatch(r"api/v1/workflows/[a-z0-9-]+", path):
        return
    if method.upper() == "PUT" and re.fullmatch(
        r"api/v1/workflows/[a-z0-9-]+/[a-z0-9-]+/(suspend|resume|terminate|stop)", path,
    ):
        return
    raise ForbiddenError("Submit an inline Workflow through Triton Control to use managed MLflow tracking")


def validate_workflow(workflow: dict[str, Any], allowed_secrets: set[str]) -> None:
    """Validate before creating any managed credential.

    A tag-enforcing proxy is ineffective if a workflow can mount its upstream
    credential, a different user's token, or the MLflow database PVC.
    Kubernetes administrators remain trusted, as they control those resources.
    """
    service_account = os.getenv("ARGO_WORKFLOWS_WORKFLOW_SERVICE_ACCOUNT", "argo-service-account")
    spec = workflow.get("spec") or {}
    if "workflowTemplateRef" in spec:
        raise BadRequestError("Managed MLflow workflows require inline templates")
    metadata = workflow.get("metadata") or {}
    if "triton-control.ai/mlflow-owner-user-id" in metadata.get("annotations", {}):
        raise ForbiddenError("MLflow workflow owner is managed by Triton Control")

    def secret(name: Any) -> None:
        if name not in allowed_secrets:
            raise ForbiddenError("Workflow may reference only the submitting user's linked S3 secrets")

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key == "artifactRepositoryRef":
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("configMap"), str)
                    or item["configMap"] not in allowed_secrets
                    or item.get("key") != REPOSITORY_KEY
                ):
                    raise ForbiddenError("Workflow may reference only the submitting user's linked S3 repositories")
            if key == "serviceAccountName" and item != service_account:
                raise ForbiddenError("Workflow must use the configured executor ServiceAccount")
            if key in {"hostNetwork", "hostPID", "hostIPC", "privileged"} and item:
                raise ForbiddenError("Managed workflows cannot access the host")
            if key in {"hostPath", "csi", "resource", "templateRef", "containerSet"}:
                raise ForbiddenError("Managed MLflow workflows require unprivileged inline container/script templates")
            if key == "capabilities" and isinstance(item, dict) and item.get("add"):
                raise ForbiddenError("Managed workflows cannot add Linux capabilities")
            if key == "podSpecPatch":
                if not isinstance(item, str) or "{{" in item:
                    raise ForbiddenError("Managed workflows require literal podSpecPatch values")
                try:
                    patch = yaml.safe_load(item)
                except (yaml.YAMLError, TypeError) as exc:
                    raise BadRequestError("Invalid workflow podSpecPatch") from exc
                visit(patch)
            elif key in {"secretKeyRef", "secretRef"} or (
                key.endswith("Secret") and isinstance(item, dict) and "name" in item
            ):
                secret(item.get("name"))
            elif key == "secretName":
                secret(item)
            elif key == "imagePullSecrets":
                for reference in item:
                    secret(reference.get("name"))
            elif key == "secret" and isinstance(item, dict) and "name" in item:
                secret(item["name"])
            elif key == "persistentVolumeClaim":
                raise ForbiddenError("Use workflow volumeClaimTemplates instead of existing volume claims")
            visit(item)

    visit(spec)
