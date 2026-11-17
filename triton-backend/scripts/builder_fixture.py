"""Disposable Kubernetes fixture. Only its newly-created namespace and S3 keys are removed."""

import base64
import hashlib
import io
import json
import os
import secrets
import socket
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from kubernetes import client as kubernetes_client  # type: ignore[import-untyped]

from app.schemas.build_policy import BuildPolicy
from app.services.builds.manifests import job, network_policy
from app.services.builds.qualification import REQUIRED_CHECKS
from app.services.kubernetes_client import api_client

REGISTRY = "registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
NGINX = "nginx@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284"
BUSYBOX = "busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"


def command(*args: str, data: str | None = None, env: dict[str, str] | None = None, timeout: int = 180) -> str:
    result = subprocess.run(args, input=data, capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode:
        # Raw command output can contain credentials; persist only safe phase identifiers.
        raise RuntimeError(f"{args[0]} failed with exit code {result.returncode}")
    return result.stdout


class Fixture:
    def __init__(self, policy: BuildPolicy, staging: dict[str, Any]) -> None:
        # The installation namespace is persistent (and may already be managed by
        # Helm). Only run disposable probes in a namespace owned by this fixture.
        self.run_id = secrets.token_hex(6)
        namespace = f"builder-qualification-{self.run_id}"
        if namespace == policy.namespace:
            namespace += "-test"
        self.policy = policy.model_copy(update={"namespace": namespace})
        self.staging = staging
        self.report: dict[str, Any] = {
            "installation_namespace": policy.namespace,
            "fixture_namespace": namespace,
            "checks": dict.fromkeys(sorted(REQUIRED_CHECKS), False),
            "failures": [],
            "transport": "isolated HTTP registry fixture; production requires trusted TLS",
            "release": policy.release,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self.processes: list[subprocess.Popen[Any]] = []
        self.keys: list[str] = []
        self.context_urls: dict[str, str] = {}
        self.created = False

    def apply(self, obj: dict[str, Any]) -> str:
        return command("kubectl", "apply", "-f", "-", data=json.dumps(obj))

    def get(self, kind: str, name: str) -> dict[str, Any]:
        with api_client() as api:
            if kind == "pod":
                model = kubernetes_client.CoreV1Api(api).read_namespaced_pod(name, self.policy.namespace)
            elif kind == "job":
                model = kubernetes_client.BatchV1Api(api).read_namespaced_job(name, self.policy.namespace)
            else:
                raise ValueError("Unsupported fixture resource")
            return cast(dict[str, Any], api.sanitize_for_serialization(model))

    def secret(self, name: str, values: dict[str, str]) -> None:
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": name, "namespace": self.policy.namespace},
                "stringData": values,
            }
        )

    def forward(self, remote: int) -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen(
            ["kubectl", "port-forward", "-n", self.policy.namespace, "pod/registry", f"{port}:{remote}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes.append(proc)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return int(port)
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError("Registry port forwarding stopped") from None
                time.sleep(0.1)
        raise RuntimeError("Registry port forwarding timed out")

    def wait_job(self, name: str, seconds: int = 360) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            current = self.get("job", name)
            if current.get("status", {}).get("succeeded"):
                return True
            if current.get("status", {}).get("failed"):
                return False
            time.sleep(2)
        return False

    def pods(self, name: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            json.loads(
                command("kubectl", "get", "pods", "-n", self.policy.namespace, "-l", f"job-name={name}", "-o", "json")
            )["items"],
        )

    def stage(self, dockerfile: str, suffix: str) -> str:
        # Explicit fixture allowlist: secret sentinel never enters the archived context.
        files = {
            "Dockerfile": dockerfile.encode(),
            "payload.txt": b"qualified-build\n",
            ".dockerignore": b"sentinel.secret\n",
            "sentinel.secret": secrets.token_bytes(32),
        }
        self.sentinel = files["sentinel.secret"]
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as archive:
            for name in ("Dockerfile", "payload.txt", ".dockerignore"):
                info = tarfile.TarInfo(name)
                info.size = len(files[name])
                archive.addfile(info, io.BytesIO(files[name]))
        data = out.getvalue()
        if len(data) > self.policy.max_context_bytes:
            raise RuntimeError("Context exceeds installation size limit")
        key = self.policy.staging_prefix + self.run_id + "/" + suffix + ".tar.gz"
        self.keys.append(key)
        result = self.s3.put_object(Bucket=self.staging["bucket"], Key=key, Body=data)
        version = result.get("VersionId")
        if not version or version == "null":
            raise RuntimeError("Qualification requires an already-versioned staging bucket")
        fetched = self.s3.get_object(Bucket=self.staging["bucket"], Key=key, VersionId=version)["Body"].read()
        self.report["checks"]["staging_roundtrip"] = hashlib.sha256(data).digest() == hashlib.sha256(fetched).digest()
        self.report.setdefault("context_sha256", {})[suffix] = hashlib.sha256(data).hexdigest()
        context = f"s3://{self.staging['bucket']}/{key}"
        url = self.s3.generate_presigned_url("get_object", Params={
            "Bucket": self.staging["bucket"], "Key": key, "VersionId": version,
        }, ExpiresIn=self.policy.timeout_seconds + 180)
        if urlsplit(url).scheme != "https":
            raise RuntimeError("Qualification requires trusted HTTPS staging reachable from the executor")
        self.context_urls[context] = url
        # A successful build must consume the recorded version, never this mutable latest object.
        self.s3.put_object(Bucket=self.staging["bucket"], Key=key, Body=b"not a build context")
        return context

    def build(self, name: str, context: str, destination: str, *, deadline: int | None = None) -> dict[str, Any]:
        manifest = job(self.policy, name, context, destination, "qualification-docker")
        pod = manifest["spec"]["template"]["spec"]
        executor = pod["containers"][0]
        executor.pop("envFrom")
        self.secret(name + "-context", {"url": self.context_urls[context]})
        executor["args"][0] = "--context=$(BUILD_CONTEXT)"
        executor["env"] = [{"name": "BUILD_CONTEXT", "valueFrom": {
            "secretKeyRef": {"name": name + "-context", "key": "url"},
        }}]
        # HTTP is limited to this disposable test registry, never the production renderer.
        manifest["spec"]["template"]["spec"]["containers"][0]["args"] += ["--insecure", "--insecure-pull"]
        if self.staging.get("ca_file"):
            pod = manifest["spec"]["template"]["spec"]
            pod["volumes"].append({"name": "staging-ca", "secret": {"secretName": "qualification-ca"}})
            pod["containers"][0]["volumeMounts"].append(
                {"name": "staging-ca", "mountPath": "/qualification-ca", "readOnly": True}
            )
            executor["env"].append({"name": "SSL_CERT_DIR", "value": "/kaniko/ssl/certs:/qualification-ca"})
        if deadline:
            manifest["spec"]["activeDeadlineSeconds"] = deadline
        self.apply(manifest)
        return manifest

    def run(self) -> dict[str, Any]:
        phase = "provenance"
        try:
            identity = f"https://github.com/osscontainertools/kaniko/.github/workflows/images.yaml@refs/tags/{self.policy.release}"
            verified = command(
                "cosign",
                "verify",
                "--certificate-identity",
                identity,
                "--certificate-oidc-issuer",
                "https://token.actions.githubusercontent.com",
                self.policy.executor_image,
            )
            self.report["provenance_sha256"] = hashlib.sha256(verified.encode()).hexdigest()
            self.report["signer_identity"] = identity
            self.report["checks"]["provenance"] = bool(json.loads(verified))
            phase = "provisioning"
            # Create, never adopt an existing namespace.
            command("kubectl", "create", "namespace", self.policy.namespace)
            self.created = True
            self.apply(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": self.policy.namespace,
                        "labels": {"pod-security.kubernetes.io/enforce": "baseline"},
                    },
                }
            )
            with tempfile.TemporaryDirectory(prefix="builder-qualification-") as temporary:
                self.execute(Path(temporary))
        except Exception as exc:
            self.report["failures"].append({"phase": phase, "type": type(exc).__name__})
        finally:
            cleanup_ok = True
            for proc in self.processes:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    cleanup_ok = False
            if hasattr(self, "s3"):
                for key in self.keys:
                    try:
                        versions = self.s3.list_object_versions(Bucket=self.staging["bucket"], Prefix=key)
                        for obj in versions.get("Versions", []) + versions.get("DeleteMarkers", []):
                            if obj["Key"] == key:
                                self.s3.delete_object(
                                    Bucket=self.staging["bucket"], Key=key, VersionId=obj["VersionId"],
                                )
                    except Exception:
                        cleanup_ok = False
            if self.created:
                try:
                    command("kubectl", "delete", "namespace", self.policy.namespace, "--wait=true", "--timeout=90s")
                except Exception:
                    cleanup_ok = False
            self.report["checks"]["cleanup"] = cleanup_ok
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.report["failures"] += [
                {"phase": key, "type": "CheckFailed"} for key, passed in self.report["checks"].items() if not passed
            ]
        return self.report

    def execute(self, temporary: Path) -> None:
        ns = self.policy.namespace
        passwords = {role: secrets.token_urlsafe(24) for role in ("base", "push", "runtime")}
        hashes = {
            role: command("openssl", "passwd", "-apr1", "-stdin", data=password).strip()
            for role, password in passwords.items()
        }
        self.secret(
            "registry-auth",
            {role: f"{role}:{value}\n" for role, value in hashes.items()}
            | {"readers": f"push:{hashes['push']}\nruntime:{hashes['runtime']}\n"},
        )
        nginx = """events {}
http { client_max_body_size 0; access_log off;
server { listen 5001; location /v2/ { auth_basic "registry"; auth_basic_user_file /auth/base;
limit_except GET HEAD { deny all; } proxy_pass http://127.0.0.1:5000; } }
server { listen 5002; location = /v2/ { auth_basic "registry";
auth_basic_user_file /auth/readers; proxy_pass http://127.0.0.1:5000; }
location /v2/allowed/ { auth_basic "registry"; auth_basic_user_file /auth/readers;
limit_except GET HEAD { auth_basic_user_file /auth/push; } proxy_pass http://127.0.0.1:5000; }
location / { return 403; } } }
"""
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "registry-config", "namespace": ns},
                "data": {"nginx.conf": nginx},
            }
        )
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "registry", "namespace": ns, "labels": {"app": "qualification-registry"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "registry",
                            "image": REGISTRY,
                            "env": [{"name": "REGISTRY_HTTP_RELATIVEURLS", "value": "true"}],
                        },
                        {
                            "name": "proxy",
                            "image": NGINX,
                            "volumeMounts": [
                                {"name": "config", "mountPath": "/etc/nginx/nginx.conf", "subPath": "nginx.conf"},
                                {"name": "auth", "mountPath": "/auth", "readOnly": True},
                            ],
                        },
                    ],
                    "volumes": [
                        {"name": "config", "configMap": {"name": "registry-config"}},
                        {"name": "auth", "secret": {"secretName": "registry-auth"}},
                    ],
                },
            }
        )
        command("kubectl", "wait", "pod/registry", "-n", ns, "--for=condition=Ready", "--timeout=180s")
        pod_ip = self.get("pod", "registry")["status"]["podIP"]
        # Explicit service address is part of the caller's policy; no hidden egress broadening.
        registry_ip = self.policy.allowed_repositories[0].split(":")[0]
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "registry", "namespace": ns},
                "spec": {
                    "clusterIP": registry_ip,
                    "selector": {"app": "qualification-registry"},
                    "ports": [
                        {"name": "base", "port": 5001},
                        {"name": "push", "port": 5002},
                        {"name": "blocked", "port": 5000},
                    ],
                },
            }
        )
        seed_port = self.forward(5000)
        push_port = self.forward(5002)
        env = {**os.environ, "DOCKER_CONFIG": str(temporary)}

        def auth(role: str) -> str:
            return base64.b64encode(f"{role}:{passwords[role]}".encode()).decode()

        (temporary / "config.json").write_text(
            json.dumps({"auths": {f"127.0.0.1:{push_port}": {"auth": auth("push")}}})
        )
        command(
            "crane",
            "copy",
            "--insecure",
            "--platform",
            self.policy.platform,
            BUSYBOX,
            f"127.0.0.1:{seed_port}/base:v1",
            env=env,
        )
        base_digest = command("crane", "digest", "--insecure", f"127.0.0.1:{seed_port}/base:v1", env=env).strip()
        self.report["base_digest"] = base_digest
        base_port = self.forward(5001)
        unauthenticated = subprocess.run(
            ["crane", "digest", "--insecure", f"127.0.0.1:{base_port}/base:v1"],
            env=env,
            capture_output=True,
            timeout=30,
        )
        if unauthenticated.returncode == 0:
            raise RuntimeError("Private base is accessible without credentials")
        self.secret(
            "qualification-docker",
            {
                "config.json": json.dumps(
                    {
                        "auths": {
                            f"{registry_ip}:5001": {"auth": auth("base")},
                            f"{registry_ip}:5002": {"auth": auth("push")},
                        }
                    }
                )
            },
        )
        for role, name in [
            ("base", self.policy.base_pull_secret),
            ("push", self.policy.push_secret),
            ("runtime", self.policy.runtime_pull_secret),
        ]:
            self.secret(
                name,
                {
                    "config.json": json.dumps(
                        {"auths": {f"{registry_ip}:{5001 if role == 'base' else 5002}": {"auth": auth(role)}}}
                    )
                },
            )
        self.secret(
            "qualification-staging",
            {
                "AWS_ACCESS_KEY_ID": self.staging["access_key"],
                "AWS_SECRET_ACCESS_KEY": self.staging["secret_key"],
                "AWS_REGION": self.staging.get("region", "us-east-1"),
                "S3_ENDPOINT": self.staging.get("pod_endpoint", self.staging["endpoint"]),
                "S3_FORCE_PATH_STYLE": "true",
            },
        )
        if self.staging.get("ca_file"):
            self.secret("qualification-ca", {"ca.pem": Path(self.staging["ca_file"]).read_text()})
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.staging["endpoint"],
            aws_access_key_id=self.staging["access_key"],
            aws_secret_access_key=self.staging["secret_key"],
            region_name=self.staging.get("region", "us-east-1"),
            verify=self.staging.get("ca_file", True),
            config=Config(s3={"addressing_style": "path"}),
        )
        self.apply(network_policy(self.policy))
        base = f"{registry_ip}:5001/base@{base_digest}"
        context = self.stage(
            f"FROM {base}\nCOPY . /qualification/\n"
            "RUN test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token "
            "&& test ! -S /var/run/docker.sock && test ! -e /qualification/sentinel.secret\n",
            "context",
        )
        destination = self.policy.allowed_repositories[0] + ":qualified"
        self.build("build", context, destination)
        passed = self.wait_job("build", self.policy.timeout_seconds + 60)
        if not passed:
            logs = command("kubectl", "logs", "job/build", "-n", ns)
            for value in [*passwords.values(), self.staging["access_key"], self.staging["secret_key"]]:
                logs = logs.replace(value, "[redacted]")
            from app.services.builds.jobs_kubernetes import redact
            self.report["build_log_tail"] = redact(logs[-4000:], list(self.context_urls.values()))
            self.report["failures"].append({"phase": "build", "type": "JobFailed"})
            return
        self.report["checks"].update(
            dict.fromkeys(
                ["private_base_pull", "scoped_push", "no_host_socket", "no_service_account_token",
                 "pod_policy", "versioned_https_context"], True
            )
        )
        pod = self.pods("build")[0]
        self.report["executor_runtime_image_id"] = pod["status"]["containerStatuses"][0]["imageID"]
        self.report["node_name"] = pod["spec"]["nodeName"]
        digest = pod["status"]["containerStatuses"][0]["state"]["terminated"].get("message", "").strip()
        remote = f"127.0.0.1:{push_port}/allowed:qualified"
        measured = command("crane", "digest", "--insecure", remote, env=env).strip()
        self.report["checks"]["digest_verified"] = digest == measured
        self.report["final_digest"] = measured
        image_tar = temporary / "image.tar"
        command("crane", "export", "--insecure", remote, str(image_tar), env=env)
        with tarfile.open(image_tar) as image:
            names = image.getnames()
            self.report["checks"]["context_exclusion"] = not any("sentinel.secret" in name for name in names)
        # Exercise registry authorization directly, bypassing the application allowlist.
        denied = subprocess.run(
            ["crane", "copy", "--insecure", remote, f"127.0.0.1:{push_port}/forbidden:denied"],
            env=env,
            capture_output=True,
        )
        self.report["checks"]["denied_push"] = denied.returncode != 0 and b"403" in denied.stderr
        (temporary / "config.json").write_text(
            json.dumps({"auths": {f"127.0.0.1:{push_port}": {"auth": auth("runtime")}}})
        )
        self.report["checks"]["runtime_pull"] = (
            command("crane", "digest", "--insecure", remote, env=env).strip() == measured
        )
        slow = self.stage(f"FROM {base}\nRUN sleep 120\n", "slow")
        self.build("cancel", slow, destination)
        time.sleep(5)
        command("kubectl", "delete", "job/cancel", "-n", ns, "--cascade=foreground", "--wait=true", "--timeout=60s")
        self.report["checks"]["cancellation"] = not self.pods("cancel")
        self.build("timeout", slow, destination, deadline=10)
        self.wait_job("timeout", 60)
        self.report["checks"]["timeout"] = any(
            c.get("reason") == "DeadlineExceeded"
            for c in self.get("job", "timeout").get("status", {}).get("conditions", [])
        )
        probe = job(self.policy, "network-probe", context, destination, "qualification-docker")
        container = probe["spec"]["template"]["spec"]["containers"][0]
        container.update(
            {
                "image": BUSYBOX,
                "command": ["sh", "-c"],
                "args": [f"if wget -q -T 3 -O /dev/null http://{pod_ip}:5000/v2/; then exit 1; fi"],
            }
        )
        self.apply(probe)
        self.report["checks"]["network_policy"] = self.wait_job("network-probe", 45)
