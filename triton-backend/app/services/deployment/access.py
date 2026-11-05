"""Reconcile deployment exposure without restarting the serving workload."""

from __future__ import annotations

import os
from typing import Any

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from kubernetes.config.config_exception import ConfigException  # type: ignore[import-untyped]
from urllib3.exceptions import HTTPError

from app.core.access_control import require_member_or_admin
from app.exceptions import BadGatewayError, BadRequestError, ConflictError
from app.repositories import instances
from app.schemas.deployment_access import DeploymentAccessRequest, DeploymentAccessResponse
from app.services.access import get_instance_or_404
from app.services.deployment.access_manifests import (
    AUTH_PREFIX,
    AUTH_SECRET_ANNOTATION,
    MIDDLEWARE_ANNOTATION,
    OWNER_LABEL,
    access_manifests,
    ingress_class,
    validate_policy,
)
from app.services.deployment.hostname import API_REQUEST_TIMEOUT, validate_hostname_available
from app.services.deployment.policy import resolve_hostname, short_hostname, validate_endpoint_policy, validate_ingress
from app.services.kubernetes_client import api_client


def validate_new_hostname(request: DeploymentAccessRequest) -> None:
    """Return an immediate conflict before creating a database deployment record."""
    if not request.ingress_host:
        return
    try:
        with api_client() as api:
            net = client.NetworkingV1Api(api)
            validate_controller(net, request)
            validate_hostname_available(net, request.ingress_host, ingress_class())
    except (ApiException, ConfigException, HTTPError, OSError) as exc:
        raise BadGatewayError("Could not verify hostname availability in Kubernetes") from exc


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
        ingress = net.read_namespaced_ingress(
            f"{instance.deployment_name}-ingress", instance.deployment_namespace,
            _request_timeout=API_REQUEST_TIMEOUT,
        )
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
        hostname=short_hostname(host),
        ingress_scheme=scheme,
        ingress_class_name=ingress.spec.ingress_class_name,
        basic_auth_enabled=bool(
            (annotations.get(AUTH_SECRET_ANNOTATION) and annotations.get(MIDDLEWARE_ANNOTATION))
            or annotations.get("nginx.ingress.kubernetes.io/auth-type") == "basic"
        ),
        tls_secret_name=tls_secret,
    )


def get_access(session: Any, claims: dict[str, Any], instance_id: int) -> DeploymentAccessResponse:
    instance = _managed_instance(session, claims, instance_id)
    return read_instance_access(instance)


def read_instance_access(instance: Any) -> DeploymentAccessResponse:
    """Read exposure for an instance whose caller has already checked ownership."""
    try:
        with api_client() as api:
            return _response(instance, _read_ingress(client.NetworkingV1Api(api), instance))
    except (ApiException, ConfigException, HTTPError, OSError) as exc:
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
    validate_ingress(request)
    namespace, name = instance.deployment_namespace, instance.deployment_name
    try:
        with api_client() as api:
            net, core = client.NetworkingV1Api(api), client.CoreV1Api(api)
            validate_controller(net, request)
            # Require the serving workload to exist (creation may still be queued).
            workload = client.AppsV1Api(api).read_namespaced_deployment(name, namespace)
            previous = _read_ingress(net, instance)
            current = _response(instance, previous)
            request = resolve_hostname(request, existing_host=current.ingress_host)
            validate_endpoint_policy(
                request, session, existing_host=current.ingress_host, existing_scheme=current.ingress_scheme,
            )
            annotations = (previous.metadata.annotations or {}) if previous else {}
            old_secret = annotations.get(AUTH_SECRET_ANNOTATION) or annotations.get(
                "nginx.ingress.kubernetes.io/auth-secret"
            )
            retained_secret = None
            if request.basic_auth_enabled and not request.basic_auth_password and old_secret:
                secret = core.read_namespaced_secret(old_secret, namespace)
                if (secret.metadata.labels or {}).get(OWNER_LABEL) == name and (secret.data or {}).get("users"):
                    retained_secret = old_secret
            manifests = access_manifests(
                request,
                namespace,
                name,
                instance.deployment_service_name,
                auth_secret=retained_secret,
                allow_metrics=bool(instance.metrics_url),
            )
            validate_hostname_available(
                net, request.ingress_host, ingress_class(),
                existing_ingress=(namespace, f"{name}-ingress") if previous else None,
            )
            if request.ingress_host:
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
                elif manifest["kind"] == "Middleware":
                    if new_secret:
                        try:
                            create_auth_middleware(api, namespace, manifest)
                        except ApiException:
                            _delete_secret(core, namespace, new_secret, name)
                            raise
                    else:
                        validate_auth_middleware(api, namespace, manifest)
                else:
                    # Preserve unrelated annotations, but explicitly remove old auth/TLS settings.
                    preserved = {
                        key: value
                        for key, value in annotations.items()
                        if key
                        not in {
                            AUTH_SECRET_ANNOTATION, MIDDLEWARE_ANNOTATION,
                            f"{AUTH_PREFIX}router.tls", f"{AUTH_PREFIX}router.entrypoints",
                            "cert-manager.io/cluster-issuer",
                            "nginx.ingress.kubernetes.io/auth-type", "nginx.ingress.kubernetes.io/auth-secret",
                            "nginx.ingress.kubernetes.io/auth-realm", "nginx.ingress.kubernetes.io/ssl-redirect",
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
    except (HTTPError, OSError) as exc:
        # A transport failure may occur after a write committed; never delete
        # newly referenced credentials without knowing the live Ingress state.
        raise BadGatewayError("Kubernetes connection failed; reload access settings before retrying") from exc
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
    if not request.ingress_host:
        return
    try:
        controller = net.read_ingress_class(ingress_class())
    except ApiException as exc:
        if exc.status in {403, 404}:
            raise BadRequestError(
                "Cannot verify the Traefik class; ask an administrator to configure it"
            ) from exc
        raise
    if controller.spec.controller != "traefik.io/ingress-controller":
        raise BadRequestError("Deployment Ingress is supported only by the Traefik controller")


def create_auth_middleware(api: Any, namespace: str, manifest: dict[str, Any]) -> None:
    """Own the immutable Middleware by its Secret, so rotation/deletion cleans up both."""
    secret = client.CoreV1Api(api).read_namespaced_secret(manifest["metadata"]["name"], namespace)
    manifest["metadata"]["ownerReferences"] = [{
        "apiVersion": "v1", "kind": "Secret", "name": secret.metadata.name, "uid": secret.metadata.uid,
    }]
    client.CustomObjectsApi(api).create_namespaced_custom_object(
        "traefik.io", "v1alpha1", namespace, "middlewares", manifest,
    )


def validate_auth_middleware(api: Any, namespace: str, manifest: dict[str, Any]) -> None:
    """Never silently retain credentials behind a missing or modified authentication middleware."""
    try:
        existing = client.CustomObjectsApi(api).get_namespaced_custom_object(
            "traefik.io", "v1alpha1", namespace, "middlewares", manifest["metadata"]["name"],
        )
    except ApiException as exc:
        if exc.status == 404:
            raise BadRequestError("Authentication middleware is missing; enter credentials again") from exc
        raise
    if (existing.get("metadata", {}).get("labels", {}).get(OWNER_LABEL)
            != manifest["metadata"]["labels"][OWNER_LABEL] or existing.get("spec") != manifest["spec"]):
        raise BadRequestError("Authentication middleware changed; enter credentials again")
