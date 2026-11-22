"""Fixed executor pod contract; users cannot supply manifests or arbitrary flags."""

from typing import Any
from urllib.parse import urlsplit

from app.schemas.build_policy import BuildPolicy

DOCKERFILE_CONTEXT = "tar:///kaniko/build-input/context.tar.gz"


def job(policy: BuildPolicy, name: str, context: str, destination: str, credential_secret: str) -> dict[str, Any]:
    if not policy.permits_destination(destination):
        raise ValueError("Destination is outside the registry repository allowlist")
    parsed = urlsplit(context)
    key = parsed.path.lstrip("/")
    if context != DOCKERFILE_CONTEXT and (
        parsed.scheme != "s3"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or not key.startswith(policy.staging_prefix)
        or ".." in key.split("/")
    ):
        raise ValueError("Context must be inside the policy staging prefix")
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": policy.namespace},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": policy.timeout_seconds,
            "ttlSecondsAfterFinished": 600,
            "template": {
                "metadata": {"labels": {"app": "builder-qualification"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/arch": policy.platform.split("/")[1]},
                    "securityContext": {"seccompProfile": {"type": policy.seccomp_profile}},
                    "containers": [
                        {
                            "name": "executor",
                            "image": policy.executor_image,
                            "args": [
                                f"--context={context}",
                                "--dockerfile=Dockerfile",
                                f"--destination={destination}",
                                "--digest-file=/dev/termination-log",
                                "--cache=false",
                            ],
                            "securityContext": {
                                "runAsUser": policy.run_as_user,
                                "privileged": False,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"], "add": list(policy.capabilities)},
                            },
                            "resources": {
                                "requests": {"cpu": f"{policy.cpu_millis}m", "memory": f"{policy.memory_mib}Mi"},
                                "limits": {"cpu": f"{policy.cpu_millis}m", "memory": f"{policy.memory_mib}Mi"},
                            },
                            "envFrom": [{"secretRef": {"name": "qualification-staging"}}],
                            "volumeMounts": [{"name": "credentials", "mountPath": "/kaniko/.docker", "readOnly": True}],
                        }
                    ],
                    "volumes": [{"name": "credentials", "secret": {"secretName": credential_secret}}],
                },
            },
        },
    }


def network_policy(policy: BuildPolicy) -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "builder-isolation", "namespace": policy.namespace},
        "spec": {
            "podSelector": {"matchLabels": {"app": "builder-qualification"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [
                {
                    "to": [
                        {
                            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                        }
                    ],
                    "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
                },
                {
                    "to": [{"ipBlock": {"cidr": cidr}} for cidr in policy.egress_cidrs],
                    "ports": [{"protocol": "TCP", "port": port} for port in policy.egress_ports],
                },
            ],
        },
    }
