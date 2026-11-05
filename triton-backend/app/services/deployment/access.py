"""Reconcile deployment exposure without restarting the serving workload."""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

import bcrypt
from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from kubernetes.config.config_exception import ConfigException  # type: ignore[import-untyped]

from app.core.access_control import require_member_or_admin
from app.exceptions import BadGatewayError, BadRequestError, ConflictError
from app.repositories import instances
from app.schemas.deployment_access import DeploymentAccessRequest, DeploymentAccessResponse
from app.services.access import get_instance_or_404
from app.services.kubernetes_client import api_client

AUTH_PREFIX = "nginx.ingress.kubernetes.io/"
OWNER_LABEL = "triton-control.io/deployment"


def ingress_class(request: DeploymentAccessRequest) -> str:
    return request.ingress_class_name or os.getenv("TRITON_DEPLOY_INGRESS_CLASS", "nginx").strip() or "nginx"


def validate_policy(request: DeploymentAccessRequest, *, creating: bool = False) -> None:
    if request.basic_auth_enabled:
        supported_class = os.getenv("TRITON_DEPLOY_INGRESS_CLASS", "nginx").strip() or "nginx"
        if ingress_class(request) != supported_class:
            raise BadRequestError("Basic authentication requires the administrator-configured ingress-nginx class")
        if creating and not request.basic_auth_password:
            raise BadRequestError("Username and password are required when enabling basic authentication")


def auth_secret_name(deployment_name: str) -> str:
    digest = hashlib.sha256(deployment_name.encode()).hexdigest()[:8]
    return f"{deployment_name[:32]}-{digest}-auth-{uuid.uuid4().hex[:8]}"


def access_manifests(
    request: DeploymentAccessRequest,
    namespace: str,
    deployment_name: str,
    service_name: str,
    *,
    auth_secret: str | None = None,
    allow_metrics: bool = True,
) -> list[dict[str, Any]]:
    validate_policy(request)
    if not request.ingress_host:
        return []
    manifests: list[dict[str, Any]] = []
    annotations: dict[str, str] = {}
    if request.basic_auth_enabled:
        if request.basic_auth_password and request.basic_auth_username:
            auth_secret = auth_secret_name(deployment_name)
            hashed = bcrypt.hashpw(request.basic_auth_password.get_secret_value().encode(), bcrypt.gensalt()).decode()
            manifests.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {"name": auth_secret, "namespace": namespace, "labels": {OWNER_LABEL: deployment_name}},
                    "stringData": {"auth": f"{request.basic_auth_username}:{hashed}\n"},
                }
            )
        if not auth_secret:
            raise BadRequestError("Username and password are required when enabling basic authentication")
        annotations.update(
            {
                f"{AUTH_PREFIX}auth-type": "basic",
                f"{AUTH_PREFIX}auth-secret": auth_secret,
                f"{AUTH_PREFIX}auth-realm": "Triton authentication required",
            }
        )
    spec: dict[str, Any] = {
        "ingressClassName": ingress_class(request),
        "rules": [
            {
                "host": request.ingress_host,
                "http": {
                    "paths": [
                        {
                            "path": path,
                            "pathType": "Prefix",
                            "backend": {"service": {"name": service_name, "port": {"name": port}}},
                        }
                        for path, port in ([("/metrics", "metrics")] if allow_metrics else []) + [("/", "http")]
                    ]
                },
            }
        ],
    }
    if (request.ingress_scheme or "https") == "https":
        tls: dict[str, Any] = {"hosts": [request.ingress_host]}
        secret = os.getenv("TRITON_DEPLOY_INGRESS_TLS_SECRET", "").strip()
        if secret:
            tls["secretName"] = secret
        spec["tls"] = [tls]
        annotations[f"{AUTH_PREFIX}ssl-redirect"] = "true"
    else:
        annotations[f"{AUTH_PREFIX}ssl-redirect"] = "false"
    manifests.append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{deployment_name}-ingress",
                "namespace": namespace,
                "labels": {OWNER_LABEL: deployment_name},
                "annotations": annotations,
            },
            "spec": spec,
        }
    )
    return manifests


def _managed_instance(session: Any, claims: dict[str, Any], instance_id: int) -> Any:
    instance = get_instance_or_404(session, instance_id, claims)
    if not (
        instance.is_self_deployed
        and instance.deployment_namespace
        and instance.deployment_name
        and instance.deployment_service_name
    ):
        raise BadRequestError("Instance is not a managed Kubernetes deployment")
    return instance


def _read_ingress(net: Any, instance: Any) -> Any:
    try:
        ingress = net.read_namespaced_ingress(f"{instance.deployment_name}-ingress", instance.deployment_namespace)
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise
    # Older deployments have no ownership label. Their backend still identifies them.
    rules = ingress.spec.rules or []
    if not rules or any(
        not rule.http
        or not rule.http.paths
        or any(
            not path.backend.service or path.backend.service.name != instance.deployment_service_name
            for path in rule.http.paths
        )
        for rule in rules
    ):
        raise ConflictError("Ingress name belongs to a different service")
    return ingress


def _response(instance: Any, ingress: Any) -> DeploymentAccessResponse:
    internal = f"http://{instance.deployment_service_name}.{instance.deployment_namespace}.svc.cluster.local:18000"
    if ingress is None:
        return DeploymentAccessResponse(access_mode="internal", url=internal, internal_url=internal)
    host = ingress.spec.rules[0].host
    scheme = "https" if ingress.spec.tls else "http"
    annotations = ingress.metadata.annotations or {}
    tls_secret = ingress.spec.tls[0].secret_name if ingress.spec.tls else None
    return DeploymentAccessResponse(
        access_mode="ingress",
        url=f"{scheme}://{host}",
        internal_url=internal,
        ingress_host=host,
        ingress_scheme=scheme,
        ingress_class_name=ingress.spec.ingress_class_name,
        basic_auth_enabled=annotations.get(f"{AUTH_PREFIX}auth-type") == "basic",
        tls_secret_name=tls_secret,
    )


def get_access(session: Any, claims: dict[str, Any], instance_id: int) -> DeploymentAccessResponse:
    instance = _managed_instance(session, claims, instance_id)
    try:
        with api_client() as api:
            return _response(instance, _read_ingress(client.NetworkingV1Api(api), instance))
    except (ApiException, ConfigException) as exc:
        raise BadGatewayError("Could not read deployment access from Kubernetes") from exc


def _delete_secret(core: Any, namespace: str, name: str | None, deployment_name: str) -> None:
    if not name:
        return
    try:
        secret = core.read_namespaced_secret(name, namespace)
        if (secret.metadata.labels or {}).get(OWNER_LABEL) == deployment_name:
            core.delete_namespaced_secret(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise


def update_access(
    request: DeploymentAccessRequest,
    session: Any,
    claims: dict[str, Any],
    instance_id: int,
) -> DeploymentAccessResponse:
    require_member_or_admin(claims)
    instance = _managed_instance(session, claims, instance_id)
    validate_policy(request)
    namespace, name = instance.deployment_namespace, instance.deployment_name
    try:
        with api_client() as api:
            net, core = client.NetworkingV1Api(api), client.CoreV1Api(api)
            validate_controller(net, request)
            # Require the serving workload to exist (creation may still be queued).
            workload = client.AppsV1Api(api).read_namespaced_deployment(name, namespace)
            previous = _read_ingress(net, instance)
            annotations = (previous.metadata.annotations or {}) if previous else {}
            old_secret = annotations.get(f"{AUTH_PREFIX}auth-secret")
            retained_secret = None
            if request.basic_auth_enabled and not request.basic_auth_password and old_secret:
                secret = core.read_namespaced_secret(old_secret, namespace)
                if (secret.metadata.labels or {}).get(OWNER_LABEL) == name and (secret.data or {}).get("auth"):
                    retained_secret = old_secret
            manifests = access_manifests(
                request,
                namespace,
                name,
                instance.deployment_service_name,
                auth_secret=retained_secret,
                allow_metrics=bool(instance.metrics_url),
            )
            if request.ingress_host:
                for other in net.list_namespaced_ingress(namespace).items:
                    if other.metadata.name != f"{name}-ingress" and any(
                        rule.host == request.ingress_host for rule in other.spec.rules or []
                    ):
                        raise ConflictError("This Ingress hostname is already used in the deployment namespace")
                validate_tls_secret(core, request, namespace)
            new_secret = None
            result = None
            for manifest in manifests:
                if manifest["kind"] == "Secret":
                    manifest["metadata"]["ownerReferences"] = [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "Deployment",
                            "name": name,
                            "uid": workload.metadata.uid,
                        }
                    ]
                    core.create_namespaced_secret(namespace, manifest)
                    new_secret = manifest["metadata"]["name"]
                else:
                    # Preserve unrelated annotations, but explicitly remove old auth/TLS settings.
                    preserved = {
                        key: value
                        for key, value in annotations.items()
                        if key
                        not in {
                            f"{AUTH_PREFIX}auth-type",
                            f"{AUTH_PREFIX}auth-secret",
                            f"{AUTH_PREFIX}auth-realm",
                            f"{AUTH_PREFIX}ssl-redirect",
                        }
                    }
                    manifest["metadata"]["annotations"] = {**preserved, **manifest["metadata"]["annotations"]}
                    try:
                        if previous:
                            manifest["metadata"]["labels"] = {
                                **(previous.metadata.labels or {}), **manifest["metadata"]["labels"]
                            }
                            if previous.metadata.owner_references:
                                manifest["metadata"]["ownerReferences"] = previous.metadata.owner_references
                            if previous.metadata.finalizers:
                                manifest["metadata"]["finalizers"] = previous.metadata.finalizers
                            manifest["metadata"]["resourceVersion"] = previous.metadata.resource_version
                            result = net.replace_namespaced_ingress(f"{name}-ingress", namespace, manifest)
                        else:
                            result = net.create_namespaced_ingress(namespace, manifest)
                    except ApiException:
                        _delete_secret(core, namespace, new_secret, name)
                        raise
            if not manifests and previous:
                net.delete_namespaced_ingress(
                    f"{name}-ingress",
                    namespace,
                    body={"preconditions": {"resourceVersion": previous.metadata.resource_version}},
                )
            response = _response(instance, result)
            # Management always uses cluster DNS; never store plaintext ingress credentials.
            instance.url = response.internal_url
            if instance.metrics_url:
                instance.metrics_url = response.internal_url.replace(":18000", ":18002/metrics")
            instances.save(session, instance)
            if old_secret and old_secret != retained_secret:
                _delete_secret(core, namespace, old_secret, name)
            return response
    except ApiException as exc:
        if exc.status == 409:
            raise ConflictError("Deployment access changed concurrently; reload and retry") from exc
        # Kubernetes error bodies can contain Secret data; never expose them.
        raise BadGatewayError(
            "Could not update deployment access in Kubernetes; reload access settings and retry"
        ) from exc
    except ConfigException as exc:
        raise BadGatewayError("Kubernetes configuration could not be loaded") from exc


def validate_tls_secret(core: Any, request: DeploymentAccessRequest, namespace: str) -> None:
    name = os.getenv("TRITON_DEPLOY_INGRESS_TLS_SECRET", "").strip()
    if not name or not request.ingress_host or (request.ingress_scheme or "https") != "https":
        return
    try:
        certificate = core.read_namespaced_secret(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise BadRequestError(
                "The administrator-configured TLS Secret is missing from the deployment namespace"
            ) from exc
        raise
    if certificate.type != "kubernetes.io/tls" or not all(
        (certificate.data or {}).get(key) for key in ("tls.crt", "tls.key")
    ):
        raise BadRequestError("The administrator-configured TLS Secret is invalid")


def validate_controller(net: Any, request: DeploymentAccessRequest) -> None:
    if not request.basic_auth_enabled:
        return
    try:
        controller = net.read_ingress_class(ingress_class(request))
    except ApiException as exc:
        if exc.status in {403, 404}:
            raise BadRequestError(
                "Cannot verify the ingress-nginx class; ask an administrator to configure it"
            ) from exc
        raise
    if controller.spec.controller != "k8s.io/ingress-nginx":
        raise BadRequestError("Basic authentication is supported only by the ingress-nginx controller")
