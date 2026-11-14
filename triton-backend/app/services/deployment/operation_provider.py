"""Create-only, owned Kubernetes reconciliation for frozen deployment manifests."""

from __future__ import annotations

import base64
import copy
from typing import Any, Callable, cast
from urllib.parse import quote

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

from app.exceptions import BadRequestError, ConflictError
from app.services.deployment.hostname import validate_hostname_available
from app.services.kubernetes_client import api_client

OWNER = "triton-control.io/operation"
KINDS = {
    "Secret": ("/api/v1", "secrets"), "Service": ("/api/v1", "services"),
    "ConfigMap": ("/api/v1", "configmaps"), "Deployment": ("/apis/apps/v1", "deployments"),
    "Ingress": ("/apis/networking.k8s.io/v1", "ingresses"),
    "Middleware": ("/apis/traefik.io/v1alpha1", "middlewares"),
}


class OwnershipConflict(BadRequestError):
    """A same-named resource must never be adopted or overwritten."""


def resource_key(manifest: dict[str, Any]) -> str:
    return f"{manifest['kind']}/{manifest['metadata']['name']}"


def subset(expected: Any, actual: Any, path: tuple[str, ...] = ()) -> bool:
    """Permit server defaults, but reject drift of every explicitly supplied value."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            (k in actual and subset(v, actual[k], (*path, k)))
            # Kubernetes omits the default zero delay when serializing Probe objects.
            or (k not in actual and k == "initialDelaySeconds" and v == 0
                and bool(path) and path[-1] in {"startupProbe", "livenessProbe", "readinessProbe"})
            for k, v in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            subset(a, b, path) for a, b in zip(expected, actual)
        )
    if len(path) >= 3 and path[-3] == "resources" and path[-2] in {"requests", "limits"}:
        try:
            return bool(parse_quantity(expected) == parse_quantity(actual))
        except (ValueError, TypeError):
            return False
    return bool(expected == actual)


class DeploymentProvider:
    def __init__(self) -> None:
        self.api = api_client()

    def close(self) -> None:
        self.api.close()

    def call(self, path: str, method: str, body: Any = None, query: Any = None) -> dict[str, Any]:
        return cast(dict[str, Any], self.api.call_api(
            path, method, body=body, query_params=query or [],
            auth_settings=["BearerToken"], response_type="object", _return_http_data_only=True,
            header_params={"Accept": "application/json", "Content-Type": "application/json"},
            _request_timeout=(3, 10),
        ))

    def path(self, manifest: dict[str, Any]) -> str:
        root, plural = KINDS[manifest["kind"]]
        ns = quote(manifest["metadata"]["namespace"], safe="")
        return f"{root}/namespaces/{ns}/{plural}"

    def read(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self.call(f"{self.path(manifest)}/{manifest['metadata']['name']}", "GET")
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def preflight(self, manifests: list[dict[str, Any]]) -> None:
        for ingress in (m for m in manifests if m["kind"] == "Ingress"):
            meta, spec = ingress["metadata"], ingress["spec"]
            class_name = spec["ingressClassName"]
            controller = self.call(f"/apis/networking.k8s.io/v1/ingressclasses/{class_name}", "GET")
            if controller["spec"]["controller"] != "traefik.io/ingress-controller":
                raise BadRequestError("Configured ingress controller changed")
            try:
                validate_hostname_available(
                    client.NetworkingV1Api(self.api), spec["rules"][0]["host"], class_name,
                    existing_ingress=(meta["namespace"], meta["name"]),
                )
            except ConflictError:
                raise OwnershipConflict("Hostname is now in use") from None
            # Validate the frozen external TLS reference; cert-manager creates its own Secret later.
            if "cert-manager.io/cluster-issuer" not in meta.get("annotations", {}):
                for tls in spec.get("tls", []):
                    if not tls.get("secretName"):
                        continue
                    try:
                        secret = self.call(f"/api/v1/namespaces/{meta['namespace']}/secrets/{tls['secretName']}", "GET")
                    except ApiException as exc:
                        if exc.status == 404:
                            raise BadRequestError("Configured TLS Secret missing") from None
                        raise
                    if secret.get("type") != "kubernetes.io/tls" or not all(
                        secret.get("data", {}).get(k) for k in ("tls.crt", "tls.key")
                    ):
                        raise BadRequestError("Configured TLS Secret invalid")

    def namespace(self, namespace: str, guard: Callable[[], None]) -> None:
        guard()
        try:
            self.call(f"/api/v1/namespaces/{namespace}", "GET")
        except ApiException as exc:
            if exc.status != 404:
                raise
            guard()
            try:
                self.call("/api/v1/namespaces", "POST", {"apiVersion": "v1", "kind": "Namespace",
                                                         "metadata": {"name": namespace}})
            except ApiException as conflict:
                if conflict.status != 409:
                    raise

    def verify(self, actual: dict[str, Any], operation_id: str, uid: str | None) -> None:
        meta = actual["metadata"]
        if meta.get("labels", {}).get(OWNER) != operation_id or (uid and meta["uid"] != uid):
            raise OwnershipConflict("Resource ownership changed; manual inspection required")
        if meta.get("deletionTimestamp"):
            raise OwnershipConflict("Resource is being deleted; creation cannot adopt it")

    def ensure(
        self, manifest: dict[str, Any], operation_id: str, uid: str | None, guard: Callable[[], None],
    ) -> dict[str, Any]:
        guard()
        actual = self.read(manifest)
        if actual is None:
            # A previously recorded object disappearing is not permission to recreate it.
            if uid:
                raise OwnershipConflict("An owned resource disappeared; explicit cleanup is required")
            guard()
            try:
                actual = self.call(self.path(manifest), "POST", manifest)
            except ApiException as exc:
                if exc.status != 409:
                    raise
                actual = self.read(manifest)
                if actual is None:
                    raise
        self.verify(actual, operation_id, uid)
        expected = copy.deepcopy(manifest)
        if expected.get("stringData"):
            expected["data"] = {k: base64.b64encode(v.encode()).decode() for k, v in expected.pop("stringData").items()}
        if not subset(expected, actual):
            raise OwnershipConflict("An owned resource differs from the frozen creation request")
        return actual

    def delete(
        self, manifest: dict[str, Any], operation_id: str, uid: str | None, guard: Callable[[], None],
    ) -> bool:
        guard()
        actual = self.read(manifest)
        if actual is None:
            return True
        metadata = actual["metadata"]
        if metadata.get("labels", {}).get(OWNER) != operation_id or (uid and metadata["uid"] != uid):
            raise OwnershipConflict("Cleanup refused a resource with different ownership")
        if not metadata.get("deletionTimestamp"):
            guard()
            try:
                self.call(f"{self.path(manifest)}/{metadata['name']}", "DELETE", {
                    "apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground",
                    "preconditions": {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]},
                })
            except ApiException as exc:
                if exc.status != 404:
                    raise
        return False

    def pods(self, namespace: str, operation_id: str) -> list[dict[str, Any]]:
        try:
            return cast(list[dict[str, Any]], self.call(
                f"/api/v1/namespaces/{namespace}/pods", "GET", query=[("labelSelector", f"{OWNER}={operation_id}")],
            ).get("items", []))
        except ApiException as exc:
            if exc.status == 404:
                return []
            raise

    @staticmethod
    def ready(deployment: dict[str, Any]) -> bool:
        status = deployment.get("status", {})
        desired = deployment.get("spec", {}).get("replicas", 1)
        return bool(desired > 0 and status.get("observedGeneration", 0) >= deployment["metadata"].get("generation", 1)
                    and status.get("updatedReplicas", 0) == desired and status.get("availableReplicas", 0) == desired
                    and status.get("readyReplicas", 0) == desired)
