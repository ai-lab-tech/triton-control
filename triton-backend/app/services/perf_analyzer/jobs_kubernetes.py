"""Exact-resource Kubernetes operations for non-root model benchmark Jobs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]
from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.db.entities import ModelPerfJobEntity
from app.services.kubernetes_client import api_client

TIMEOUT = (5, 15)
RUN_LABEL = "triton-control/run-id"
_JOB_TEMPLATE = Path(__file__).with_name("perf_analyzer_job.yaml")


@dataclass(frozen=True)
class LogSnapshot:
    output: str = ""
    warning: str = ""


def default_image() -> str:
    return os.getenv("PERF_ANALYZER_IMAGE", "nvcr.io/nvidia/tritonserver:26.06-py3-sdk")


def deadline_seconds() -> int:
    return max(60, int(os.getenv("PERF_ANALYZER_DEADLINE_SECONDS", "3600")))


def manifest(run: ModelPerfJobEntity) -> dict[str, Any]:
    """Always create suspended; only a locked, active run can enable execution."""
    # Parse a fresh object for every run so optional fields cannot leak between Jobs.
    job = cast(dict[str, Any], yaml.safe_load(_JOB_TEMPLATE.read_text(encoding="utf-8")))
    job["metadata"].update(name=run.job_name, namespace=run.namespace)
    job["metadata"]["labels"][RUN_LABEL] = run.id
    spec = job["spec"]
    spec["activeDeadlineSeconds"] = deadline_seconds()
    spec["template"]["metadata"]["labels"][RUN_LABEL] = run.id
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    container.update(image=run.image, command=list(run.command))
    for resource_type, suffix in (("requests", "REQUEST"), ("limits", "LIMIT")):
        for resource in ("cpu", "memory"):
            values = container["resources"][resource_type]
            values[resource] = os.getenv(f"PERF_ANALYZER_{resource.upper()}_{suffix}", values[resource])
    if "/perf-input/input.json" in run.command:
        pod["volumes"].append({"name": "input", "secret": {"secretName": f"{run.job_name}-input"}})
        pod["containers"][0]["volumeMounts"].append({
            "name": "input", "mountPath": "/perf-input", "readOnly": True,
        })
    if run.pull_secret:
        pod["imagePullSecrets"] = [{"name": f"{run.job_name}-pull"}]
    return job


class Jobs:
    def __init__(self) -> None:
        self.api = api_client()
        self.batch = client.BatchV1Api(self.api)
        self.core = client.CoreV1Api(self.api)
        self.apps = client.AppsV1Api(self.api)

    def close(self) -> None:
        self.api.close()

    def read(self, run: ModelPerfJobEntity) -> Any:
        try:
            job = self.batch.read_namespaced_job(run.job_name, run.namespace, _request_timeout=TIMEOUT)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise
        self.check_owner(job, run)
        if run.job_uid and job.metadata.uid != run.job_uid:
            raise ValueError("Benchmark Job identity changed; manual investigation required")
        return job

    @staticmethod
    def check_owner(resource: Any, run: ModelPerfJobEntity) -> None:
        if (resource.metadata.labels or {}).get(RUN_LABEL) != run.id:
            raise ValueError("Resource is not owned by this benchmark run")

    def create(self, run: ModelPerfJobEntity) -> Any:
        try:
            return self.batch.create_namespaced_job(run.namespace, manifest(run), _request_timeout=TIMEOUT)
        except ApiException as exc:
            if exc.status != 409:
                raise
            return self.read(run)

    def resume(self, run: ModelPerfJobEntity, job: Any) -> None:
        self.batch.patch_namespaced_job(run.job_name, run.namespace, {
            "metadata": {"resourceVersion": job.metadata.resource_version}, "spec": {"suspend": False},
        }, _request_timeout=TIMEOUT)

    def prepare(self, run: ModelPerfJobEntity, credentials: str | None) -> None:
        if "/perf-input/input.json" in run.command:
            self.secret(run, "input", {"input.json": run.input_data or ""}, "Opaque")
        if credentials:
            self.secret(run, "pull", {".dockerconfigjson": credentials}, "kubernetes.io/dockerconfigjson")

    def secret(self, run: ModelPerfJobEntity, suffix: str, data: dict[str, str], secret_type: str) -> None:
        name = f"{run.job_name}-{suffix}"
        try:
            self.core.create_namespaced_secret(run.namespace, {
                "apiVersion": "v1", "kind": "Secret", "type": secret_type, "immutable": True,
                "metadata": {"name": name, "labels": {RUN_LABEL: run.id}}, "stringData": data,
            }, _request_timeout=TIMEOUT)
        except ApiException as exc:
            if exc.status != 409:
                raise
            self.check_owner(self.core.read_namespaced_secret(name, run.namespace, _request_timeout=TIMEOUT), run)

    def pods(self, run: ModelPerfJobEntity) -> list[Any]:
        try:
            return list(self.core.list_namespaced_pod(
                run.namespace, label_selector=f"{RUN_LABEL}={run.id}", _request_timeout=TIMEOUT,
            ).items)
        except ApiException as exc:
            if exc.status == 404:
                return []
            raise

    def logs(self, run: ModelPerfJobEntity, pods: list[Any]) -> LogSnapshot:
        output = []
        warnings = []
        for pod in pods:
            try:
                value = self.core.read_namespaced_pod_log(
                    pod.metadata.name, run.namespace, container="perf-analyzer", limit_bytes=2_000_000,
                    _request_timeout=TIMEOUT,
                )
                if value:
                    output.append(str(value))
            except ApiException as exc:
                if exc.status not in {400, 404}:
                    raise
                if pod.status.phase in {"Succeeded", "Failed"}:
                    warnings.append("Benchmark output unavailable: pod logs could not be recovered.")
        return LogSnapshot(output="\n".join(output), warning="\n".join(warnings))

    def delete_job(self, run: ModelPerfJobEntity, job: Any, pods: list[Any]) -> None:
        if job:
            try:
                self.batch.delete_namespaced_job(run.job_name, run.namespace, body=client.V1DeleteOptions(
                    propagation_policy="Foreground", preconditions=client.V1Preconditions(uid=job.metadata.uid),
                ), _request_timeout=TIMEOUT)
            except ApiException as exc:
                if exc.status != 404:
                    raise
        else:
            # A manually removed Job can leave orphaned pods; terminate those exact run-owned pods.
            for pod in pods:
                try:
                    self.core.delete_namespaced_pod(pod.metadata.name, run.namespace, body=client.V1DeleteOptions(
                        preconditions=client.V1Preconditions(uid=pod.metadata.uid),
                    ), _request_timeout=TIMEOUT)
                except ApiException as exc:
                    if exc.status != 404:
                        raise

    def cleanup(self, run: ModelPerfJobEntity) -> bool:
        job, pods = self.read(run), self.pods(run)
        self.delete_job(run, job, pods)
        if job or pods:
            return False
        for suffix in ("input", "pull"):
            name = f"{run.job_name}-{suffix}"
            try:
                secret = self.core.read_namespaced_secret(name, run.namespace, _request_timeout=TIMEOUT)
                self.check_owner(secret, run)
                self.core.delete_namespaced_secret(name, run.namespace, body=client.V1DeleteOptions(
                    preconditions=client.V1Preconditions(uid=secret.metadata.uid),
                ), _request_timeout=TIMEOUT)
            except ApiException as exc:
                if exc.status != 404:
                    raise
        return True

    def retire_legacy(self, namespace: str, name: str) -> bool:
        """Delete only the recorded deployment; never delete its namespace."""
        try:
            deployment = self.apps.read_namespaced_deployment(name, namespace, _request_timeout=TIMEOUT)
        except ApiException as exc:
            if exc.status != 404:
                raise
        else:
            if (deployment.spec.template.metadata.labels or {}).get("app") != "perf-analyzer":
                raise ValueError("Recorded legacy Deployment no longer belongs to Perf Analyzer")
            self.apps.delete_namespaced_deployment(name, namespace, body=client.V1DeleteOptions(
                propagation_policy="Foreground", preconditions=client.V1Preconditions(uid=deployment.metadata.uid),
            ), _request_timeout=TIMEOUT)
            return False
        # Detection only: never delete pods using the old shared label. Foreground
        # Deployment deletion normally removes them; manual orphaning needs attention.
        try:
            if self.core.list_namespaced_pod(
                namespace, label_selector="app=perf-analyzer", _request_timeout=TIMEOUT,
            ).items:
                return False
        except ApiException as exc:
            if exc.status != 404:
                raise
        try:
            self.core.delete_namespaced_secret(f"{name}-pull-secret", namespace, _request_timeout=TIMEOUT)
        except ApiException as exc:
            if exc.status != 404:
                raise
        return True


def pod_message(pods: list[Any]) -> str:
    for pod in pods:
        for status in pod.status.container_statuses or []:
            if status.state.waiting:
                return str(status.state.waiting.reason or "Pending") + ": " + str(status.state.waiting.message or "")
        for condition in pod.status.conditions or []:
            if condition.status == "False" and condition.message:
                return str(condition.message)
    return "Waiting for benchmark pod."
