"""Shared preflight validation for deployment Ingress hostnames."""

from __future__ import annotations

from typing import Any

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.exceptions import ConflictError

API_REQUEST_TIMEOUT = (3, 5)
TRAEFIK_CONTROLLER = "traefik.io/ingress-controller"


def _may_share_controller(net: Any, class_name: str) -> bool:
    try:
        ingress_class = net.read_ingress_class(class_name, _request_timeout=API_REQUEST_TIMEOUT)
    except ApiException as exc:
        if exc.status == 404:
            # Legacy annotation-only classes may have no IngressClass resource.
            # Their controller cannot be established, so include them conservatively.
            return True
        raise
    return bool(ingress_class.spec.controller == TRAEFIK_CONTROLLER)


def hosts_overlap(first: str, second: str) -> bool:
    """Match DNS names and Kubernetes single-label wildcard hosts."""
    first, second = first.lower().rstrip("."), second.lower().rstrip(".")
    if not first or not second:
        # A hostless Ingress rule accepts any host.
        return True
    if first == second:
        return True
    for wildcard, host in ((first, second), (second, first)):
        if wildcard.startswith("*."):
            suffix = wildcard[2:]
            if host.endswith("." + suffix):
                label = host[: -(len(suffix) + 1)]
                if label and "." not in label:
                    return True
    return False


def validate_hostname_available(
    net: Any,
    host: str | None,
    ingress_class: str,
    *,
    existing_ingress: tuple[str, str] | None = None,
) -> None:
    """Reject overlapping routes in any namespace before modifying resources.

    Unclassified Ingresses are included conservatively because Traefik may
    serve them. Other classes using Traefik are included too: a single controller
    can watch several classes. Only a verified different controller is excluded.
    """
    if not host:
        return
    shared_classes: dict[str, bool] = {}
    for ingress in net.list_ingress_for_all_namespaces(_request_timeout=API_REQUEST_TIMEOUT).items or []:
        metadata, spec = ingress.metadata, ingress.spec
        if existing_ingress == (metadata.namespace, metadata.name):
            continue
        if not any(hosts_overlap(host, rule.host or "") for rule in spec.rules or []):
            continue
        route_class = spec.ingress_class_name or (metadata.annotations or {}).get(
            "kubernetes.io/ingress.class"
        )
        if route_class and route_class != ingress_class:
            if route_class not in shared_classes:
                shared_classes[route_class] = _may_share_controller(net, route_class)
            if not shared_classes[route_class]:
                continue
        raise ConflictError(
            "This hostname overlaps an existing Ingress route. Choose a different hostname."
        )
