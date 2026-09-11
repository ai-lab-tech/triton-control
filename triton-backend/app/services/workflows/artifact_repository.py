"""Argo artifact repository configuration for linked S3 profiles."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from app.db.entities import S3ProfileEntity, WorkflowS3CredentialEntity
from app.exceptions import BadGatewayError
from app.services.kubernetes_client import api_client

REPOSITORY_KEY = "repository"
_LABELS = {
    "app.kubernetes.io/managed-by": "triton-control",
    "triton-control/component": "workflow-s3-artifact-repository",
}


def repository_data(profile: S3ProfileEntity, secret_name: str) -> str:
    endpoint = urlsplit(profile.endpoint if "://" in profile.endpoint else f"https://{profile.endpoint}")
    if endpoint.scheme not in ("https", "http") or not endpoint.hostname or endpoint.username or endpoint.password:
        raise ValueError("S3 endpoint must be an HTTP or HTTPS host")
    if endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
        raise ValueError("S3 endpoint must not contain a path, query, or fragment")
    s3 = {
        "endpoint": endpoint.netloc,
        "bucket": profile.bucket,
        "region": profile.region or "us-east-1",
        "insecure": endpoint.scheme == "http",
        "accessKeySecret": {"name": secret_name, "key": "access-key-id"},
        "secretKeySecret": {"name": secret_name, "key": "secret-access-key"},
    }
    if profile.ca_certificate and endpoint.scheme == "https":
        s3["caSecret"] = {"name": secret_name, "key": "ca.pem"}
    # JSON is valid YAML; no credentials or certificate contents belong in the ConfigMap.
    return json.dumps({"s3": s3})


def _check_owner(body) -> None:
    labels = body.metadata.labels or {}
    if any(labels.get(key) != value for key, value in _LABELS.items()):
        raise BadGatewayError("Refusing to modify an unmanaged artifact repository ConfigMap.")


def sync_repository(row: WorkflowS3CredentialEntity, profile: S3ProfileEntity) -> None:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    data = repository_data(profile, row.secret_name)
    core = client.CoreV1Api(api_client())
    try:
        body = core.read_namespaced_config_map(name=row.secret_name, namespace=row.namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core.create_namespaced_config_map(namespace=row.namespace, body=client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=row.secret_name, namespace=row.namespace, labels=dict(_LABELS)),
            data={REPOSITORY_KEY: data},
        ))
        return
    _check_owner(body)
    body.data = {**(body.data or {}), REPOSITORY_KEY: data}
    core.replace_namespaced_config_map(name=row.secret_name, namespace=row.namespace, body=body)


def delete_repository(row: WorkflowS3CredentialEntity) -> None:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    core = client.CoreV1Api(api_client())
    try:
        body = core.read_namespaced_config_map(name=row.secret_name, namespace=row.namespace)
        _check_owner(body)
        core.delete_namespaced_config_map(
            name=row.secret_name, namespace=row.namespace,
            body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=body.metadata.uid)),
        )
    except ApiException as exc:
        if exc.status != 404:
            raise BadGatewayError("Failed to delete workflow artifact repository ConfigMap.") from None
