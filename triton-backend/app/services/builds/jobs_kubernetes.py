"""Exact owned Job/Secret operations with UID fencing and bounded provider calls."""

import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.db.entities import BuildAttemptEntity
from app.exceptions import ConflictError
from app.schemas.build_policy import BuildPolicy, LocalArchivePolicy
from app.services.builds.manifests import job
from app.services.kubernetes_client import api_client

TIMEOUT = (3, 10)
LABEL = "triton-control/build-attempt"
LOG_LIMIT = 1024 * 1024
LOG_LINES = 10000
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


def registry_host(address: str) -> str:
    host = urlsplit(address if "://" in address else "//" + address).netloc.lower()
    if host in {"index.docker.io", "registry-1.docker.io"}:
        return "docker.io"
    return host


def identity(row: BuildAttemptEntity) -> str:
    return f"{row.operation_id}-{row.number}"


def attempt_policy(row: BuildAttemptEntity) -> BuildPolicy:
    model = LocalArchivePolicy if row.policy.get("local_archive") else BuildPolicy
    return model.model_validate(row.policy)


def manifest(row: BuildAttemptEntity) -> dict[str, Any]:
    policy = attempt_policy(row)
    receipt = row.context_receipt
    bucket, key = receipt["storage_binding"]["bucket"], receipt["key"]
    body = job(policy, row.job_name, f"s3://{bucket}/{key}", row.destination, row.job_name)
    body["metadata"]["labels"] = {LABEL: identity(row)}
    spec = body["spec"]
    spec.pop("ttlSecondsAfterFinished")  # Evidence must commit before deletion, even during outages.
    spec["suspend"] = True
    pod = spec["template"]
    pod["metadata"]["labels"][LABEL] = identity(row)
    container = pod["spec"]["containers"][0]
    container.pop("envFrom")  # No ambient S3 credentials in the executor.
    container["args"][0] = "--context=$(BUILD_CONTEXT)"
    container["args"][1] = "--dockerfile=" + receipt["dockerfile"]
    container["env"] = [{"name": "BUILD_CONTEXT", "valueFrom": {
        "secretKeyRef": {"name": row.job_name, "key": "context"},
    }}]
    container["env"].append({"name": "SSL_CERT_DIR", "value": "/kaniko/ssl/certs:/build-ca"})
    container["volumeMounts"].append({"name": "context-ca", "mountPath": "/build-ca", "readOnly": True})
    pod["spec"]["volumes"].append({"name": "context-ca", "secret": {
        "secretName": row.job_name, "optional": True, "items": [{"key": "ca.pem", "path": "ca.pem"}],
    }})
    if isinstance(policy, LocalArchivePolicy):
        container["args"].extend(["--no-push", "--no-push-cache", "--tar-path=/output/image.tar"])
        container["volumeMounts"].append({"name": "output", "mountPath": "/output"})
        pod["spec"]["volumes"].append({"name": "output", "persistentVolumeClaim": {"claimName": row.job_name}})
    container["terminationMessagePolicy"] = "File"
    pod["spec"]["volumes"][0]["secret"]["items"] = [{"key": "config.json", "path": "config.json"}]
    return body


def redact(output: str, values: list[str]) -> str:
    for value in sorted(set(values), key=len, reverse=True):
        if value:
            output = output.replace(value, "[REDACTED]")
    # Signed URLs may be escaped/reformatted by an executor error message.
    return re.sub(r"https?://[^\s\"<>]+", "[REDACTED_URL]", output)


@dataclass(frozen=True)
class Observation:
    terminal: bool = False
    succeeded: bool = False
    digest: str | None = None
    logs: str = ""
    truncated: bool = False
    running: bool = False


class Jobs:
    def __init__(self) -> None:
        self.api = api_client()
        self.batch = client.BatchV1Api(self.api)
        self.core = client.CoreV1Api(self.api)

    def close(self) -> None:
        self.api.close()

    @staticmethod
    def check(resource: Any, row: BuildAttemptEntity) -> None:
        if (resource.metadata.labels or {}).get(LABEL) != identity(row):
            raise ConflictError("Build resource ownership differs")

    def read(self, row: BuildAttemptEntity) -> Any:
        try:
            result = self.batch.read_namespaced_job(row.job_name, row.namespace, _request_timeout=TIMEOUT)
        except ApiException as error:
            if error.status == 404:
                return None
            raise
        self.check(result, row)
        if row.job_uid and result.metadata.uid != row.job_uid:
            raise ConflictError("Build Job UID differs; refusing adoption or deletion")
        return result

    def secret(self, row: BuildAttemptEntity) -> Any:
        try:
            result = self.core.read_namespaced_secret(row.job_name, row.namespace, _request_timeout=TIMEOUT)
        except ApiException as error:
            if error.status == 404:
                return None
            raise
        self.check(result, row)
        return result

    def provision(self, row: BuildAttemptEntity, context_url: str, ca_certificate: str = "",
                  push_config: str | None = None) -> None:
        if row.policy.get("local_archive"):
            self.ensure_output_claim(row)
        if self.secret(row) is not None:
            return
        policy = attempt_policy(row)
        # Admin-supplied credentials go directly into the immutable per-build Secret.
        auths: dict[str, Any] = {}
        names: tuple[str, ...] = (policy.base_pull_secret,)
        if not policy.anonymous_push:
            auths = json.loads(push_config)["auths"] if push_config else {}
            names = (policy.base_pull_secret,) if push_config else (policy.base_pull_secret, policy.push_secret)
        if isinstance(policy, LocalArchivePolicy):
            names = ()
        for name in names:
            try:
                secret = self.core.read_namespaced_secret(name, row.namespace, _request_timeout=TIMEOUT)
            except ApiException as error:
                if policy.anonymous_push and error.status == 404:
                    continue
                raise
            data = secret.data or {}
            config = json.loads(base64.b64decode(data.get(".dockerconfigjson", data.get("config.json", ""))))
            if set(config) != {"auths"} or not isinstance(config["auths"], dict):
                raise ConflictError("Provision explicit registry auths without external credential helpers")
            for host, value in config["auths"].items():
                if policy.anonymous_push and registry_host(host) == registry_host(row.destination):
                    continue
                if host in auths and auths[host] != value:
                    raise ConflictError("Base and push registry credential scopes overlap")
                auths[host] = value
        body: dict[str, Any] = {"apiVersion": "v1", "kind": "Secret", "immutable": True,
                "metadata": {"name": row.job_name, "namespace": row.namespace, "labels": {LABEL: identity(row)}},
                "stringData": {"context": context_url, "config.json": json.dumps({"auths": auths})}}
        if ca_certificate:
            body["stringData"]["ca.pem"] = ca_certificate
        try:
            self.core.create_namespaced_secret(row.namespace, body, _request_timeout=TIMEOUT)
        except ApiException as error:
            if error.status != 409:
                raise
            self.secret(row)

    def ensure_output_claim(self, row: BuildAttemptEntity) -> None:
        claim = {"metadata": {"name": row.job_name, "labels": {LABEL: identity(row)}},
                 "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "35Gi"}}}}
        try:
            self.core.create_namespaced_persistent_volume_claim(row.namespace, claim, _request_timeout=TIMEOUT)
        except ApiException as error:
            if error.status != 409:
                raise
            self.check(self.core.read_namespaced_persistent_volume_claim(
                row.job_name, row.namespace, _request_timeout=TIMEOUT), row)

    def create(self, row: BuildAttemptEntity) -> Any:
        try:
            result = self.batch.create_namespaced_job(row.namespace, manifest(row), _request_timeout=TIMEOUT)
        except ApiException as error:
            if error.status != 409:
                raise
            result = self.read(row)
        self.check(result, row)
        return result

    def resume(self, row: BuildAttemptEntity, resource: Any) -> bool:
        try:
            self.batch.patch_namespaced_job(row.job_name, row.namespace, {
                "metadata": {"resourceVersion": resource.metadata.resource_version}, "spec": {"suspend": False},
            }, _request_timeout=TIMEOUT)
            return True
        except ApiException as error:
            if error.status in {404, 409}:
                return False  # Controller status updates race; re-observe on the next pass.
            raise

    def sensitive_values(self, row: BuildAttemptEntity) -> list[str]:
        secret = self.secret(row)
        if secret is None:
            raise ConflictError("Build log redaction credentials are unavailable")
        values = [base64.b64decode(v).decode() for v in (secret.data or {}).values()]
        config = json.loads(base64.b64decode(secret.data["config.json"]))
        for auth in config["auths"].values():
            for key, value in auth.items():
                if isinstance(value, str):
                    values.append(value)
                    if key == "auth":
                        decoded = base64.b64decode(value).decode()
                        values.extend([decoded, *decoded.split(":", 1)])
        return values

    def observe(self, row: BuildAttemptEntity, resource: Any) -> Observation:
        pods = self.core.list_namespaced_pod(
            row.namespace, label_selector=f"{LABEL}={identity(row)}", _request_timeout=TIMEOUT,
        ).items
        owned = [pod for pod in pods if any(
            ref.uid == resource.metadata.uid and ref.kind == "Job" and ref.controller
            for ref in (pod.metadata.owner_references or [])
        )]
        if len(owned) > 1:
            raise ConflictError("Unexpected multiple executor pods; evidence is ambiguous")
        logs, digest, truncated, exited_ok, running = "", None, False, False, False
        if owned:
            pod = owned[0]
            statuses = pod.status.container_statuses or []
            executor = next((s for s in statuses if s.name == "executor"), None)
            running = bool(executor and executor.state.running)
            if executor and (executor.state.running or executor.state.terminated):
                try:
                    raw = self.core.read_namespaced_pod_log(
                        pod.metadata.name, row.namespace, container="executor", limit_bytes=LOG_LIMIT,
                        tail_lines=LOG_LINES, _request_timeout=TIMEOUT,
                    )
                except ApiException as error:
                    if error.status != 404:
                        raise
                    # Deadline/cancellation cleanup can remove a listed pod before
                    # its logs are read. Still observe the Job's terminal status.
                    running = False
                else:
                    logs = redact(raw, self.sensitive_values(row))
                    truncated = len(raw.encode()) >= LOG_LIMIT or len(raw.splitlines()) >= LOG_LINES
            if executor and executor.state.terminated:
                terminated = executor.state.terminated
                exited_ok = terminated.exit_code == 0
                message = (terminated.message or "").strip()
                if exited_ok and DIGEST.fullmatch(message):
                    digest = message
        conditions = resource.status.conditions or []
        terminal = any(c.type in {"Complete", "Failed"} and c.status == "True" for c in conditions)
        success = any(c.type == "Complete" and c.status == "True" for c in conditions)
        return Observation(terminal, success and exited_ok and digest is not None, digest, logs, truncated, running)

    def cleanup(self, row: BuildAttemptEntity) -> bool:
        resource = self.read(row)
        if resource is not None:
            self.batch.delete_namespaced_job(row.job_name, row.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                           preconditions=client.V1Preconditions(uid=resource.metadata.uid)),
                _request_timeout=TIMEOUT)
            return False
        secret = self.secret(row)
        if secret is not None:
            self.core.delete_namespaced_secret(row.job_name, row.namespace,
                body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=secret.metadata.uid)),
                _request_timeout=TIMEOUT)
        if row.policy.get("local_archive") and row.state != "succeeded":
            try:
                claim = self.core.read_namespaced_persistent_volume_claim(row.job_name, row.namespace,
                                                                          _request_timeout=TIMEOUT)
                self.check(claim, row)
                self.core.delete_namespaced_persistent_volume_claim(row.job_name, row.namespace,
                    body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=claim.metadata.uid)),
                    _request_timeout=TIMEOUT)
                return False
            except ApiException as error:
                if error.status != 404:
                    raise
        return True
