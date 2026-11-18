"""Fail closed before starting a production process with unresolved development access."""

import os
from typing import Any

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session

from app.core import installation_policy as policy
from app.repositories.installation_policy import cutover_blockers
from app.services.kubernetes_client import api_client, in_cluster_namespace, is_running_in_cluster

DEVELOPMENT_APPS = {"code-server", "mlflow", "perf-analyzer", "model-perf-analyzer", "builder-qualification"}


def development_resource(resource: Any) -> bool:
    labels = dict(resource.metadata.labels or {})
    spec = getattr(resource, "spec", None)
    template = getattr(spec, "template", None)
    if template is not None:
        labels.update(template.metadata.labels or {})
    return (labels.get("app") in DEVELOPMENT_APPS
            or labels.get("app.kubernetes.io/name") in {"argo-workflows", "mlflow"}
            or any(k in labels for k in ("triton-control/build-attempt", "triton-control/run-id",
                                        "workflows.argoproj.io/workflow"))
            or resource.metadata.name.startswith(("code-", "mlflow-", "workflow-s3-", "argo-workflows-")))


def verify_cluster_cutover() -> None:
    if not is_running_in_cluster() and not os.getenv("KUBERNETES_KUBECONFIG_PATH"):
        return
    namespaces = {value for value in (
        in_cluster_namespace(), os.getenv("TRITON_CONTROL_NAMESPACE"), os.getenv("KUBERNETES_NAMESPACE"),
        os.getenv("POD_NAMESPACE"), os.getenv("ARGO_WORKFLOWS_NAMESPACE"),
    ) if value}
    if not namespaces:
        raise RuntimeError("Production cutover requires an explicit installation namespace")
    try:
        with api_client() as api:
            core, apps, batch = client.CoreV1Api(api), client.AppsV1Api(api), client.BatchV1Api(api)
            for namespace in namespaces:
                for listing in (core.list_namespaced_pod, core.list_namespaced_service, core.list_namespaced_secret,
                                core.list_namespaced_service_account, apps.list_namespaced_deployment,
                                apps.list_namespaced_stateful_set, batch.list_namespaced_job):
                    resources = listing(namespace, _request_timeout=(3, 10)).items
                    if any(development_resource(resource) for resource in resources):
                        raise RuntimeError("Remove development workloads and access before production activation")
                try:
                    workflows = client.CustomObjectsApi(api).list_namespaced_custom_object(
                        "argoproj.io", "v1alpha1", namespace, "workflows", _request_timeout=(3, 10),
                    )
                    if workflows.get("items"):
                        raise RuntimeError("Remove development workflows before production activation")
                except ApiException as error:
                    if error.status != 404:
                        raise
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Unable to verify production cutover; check scoped Kubernetes inventory access") from None


def verify_cutover(session: Session) -> None:
    if policy.mode() != "production":
        return
    if os.getenv("ARGO_WORKFLOWS_ENABLED", "false").lower() not in {"false", "0", ""}:
        raise RuntimeError("Production cannot enable Argo Workflows")
    if os.getenv("TRITON_BUILD_POLICY_FILE"):
        raise RuntimeError("Remove development build policy configuration before production activation")
    blockers = cutover_blockers(session)
    if blockers:
        raise RuntimeError("Production cutover blocked: " + ", ".join(blockers))
    verify_cluster_cutover()
