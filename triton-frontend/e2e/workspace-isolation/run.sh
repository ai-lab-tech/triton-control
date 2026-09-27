#!/usr/bin/env bash
# Always creates an isolated cluster; never uses or deletes the caller's cluster.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
KIND=${KIND:-kind}
ISOLATION_SMOKE_PYTHON=${ISOLATION_SMOKE_PYTHON:-python3}
export ISOLATION_SMOKE_PYTHON
for tool in docker kubectl helm node npm curl "$KIND" "$ISOLATION_SMOKE_PYTHON"; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
DEFAULT_VERSION=$(node -e 'const fs=require("fs"); const source=fs.readFileSync(process.argv[1],"utf8"); const match=source.match(/codeServer:\s*\n(?:\s*#[^\n]*\n)*\s*version:\s*"([^"]+)"/); if(!match) process.exit(1); process.stdout.write(match[1]);' "$REPO_ROOT/charts/triton-control/values.yaml")
export ISOLATION_SMOKE_VERSION=${ISOLATION_SMOKE_VERSION:-$DEFAULT_VERSION}
for version in "$ISOLATION_SMOKE_VERSION"; do
  [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Expected a code-server version such as 4.125.0" >&2; exit 1; }
done
# Fail before provisioning if another service already owns the test port.
"$ISOLATION_SMOKE_PYTHON" - <<'PYPORT'
import socket
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 18080))
PYPORT
export ISOLATION_SMOKE_URL=http://127.0.0.1:18080
export ISOLATION_SMOKE_PASSWORD=$("$ISOLATION_SMOKE_PYTHON" -c 'import secrets; print("Smoke1!" + secrets.token_hex(16))')
CLUSTER="workspace-isolation-$(date +%s)-$$"
export ISOLATION_SMOKE_CONTEXT="kind-$CLUSTER"
RUNTIME=$(mktemp -d)
ARTIFACTS="$REPO_ROOT/triton-frontend/test-results/workspace-isolation-runtime"
mkdir -p "$ARTIFACTS"
chmod 700 "$RUNTIME"
export KUBECONFIG="$RUNTIME/kubeconfig"
PIDS=()
CLUSTER_CREATED=false
cleanup() {
  status=$?
  trap - EXIT
  if $CLUSTER_CREATED; then
    kubectl -n workspace-isolation get pvc -o wide > "$ARTIFACTS/pvcs.txt" 2>&1 || true
    kubectl -n workspace-isolation get pods -o wide > "$ARTIFACTS/pods.txt" 2>&1 || true
    kubectl -n workspace-isolation get events --sort-by=.metadata.creationTimestamp > "$ARTIFACTS/events.txt" 2>&1 || true
    while read -r pod; do
      kubectl -n workspace-isolation logs "$pod" --all-containers --tail=300 > "$ARTIFACTS/${pod#pod/}.log" 2>&1 || true
    done < <(kubectl -n workspace-isolation get pods -o name 2>/dev/null)
    if [[ ${ISOLATION_SMOKE_KEEP_ENV:-false} == true ]]; then
      echo "Kept isolated cluster $CLUSTER for debugging; private runner state: $RUNTIME"
      exit "$status"
    fi
    "$KIND" delete cluster --name "$CLUSTER" || status=1
  fi
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$RUNTIME"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ ${ISOLATION_SMOKE_SKIP_BUILD:-false} != true ]]; then
  docker build -t triton-control:workspace-isolation "$REPO_ROOT"
fi
docker build -t workspace-isolation-workspace:local -f "$SCRIPT_DIR/workspace.Dockerfile" "$SCRIPT_DIR"
CLUSTER_CREATED=true
"$KIND" create cluster --name "$CLUSTER" --wait 120s
"$KIND" load docker-image --name "$CLUSTER" triton-control:workspace-isolation workspace-isolation-workspace:local
kubectl create namespace workspace-isolation
# Use a temporary manifest so credentials never become shell command arguments.
"$ISOLATION_SMOKE_PYTHON" - "$RUNTIME" <<'PY'
import base64, json, os, pathlib, secrets, sys
root = pathlib.Path(sys.argv[1])
(root / "values.json").write_text(json.dumps({
    "fullnameOverride": "triton-control", "ingress": {"enabled": False},
    "app": {"image": {"repository": "triton-control", "tag": "workspace-isolation"},
            "resources": {"limits": {"cpu": "2", "memory": "2Gi"}},
            "env": [{"name": k, "value": v} for k, v in {
                "OIDC_ENABLED": "false", "OIDC_CONFIG_SOURCE": "env",
                "SERVER_HTTPS_ENABLED": "false", "SESSION_HTTPS_ONLY": "false",
                "EMAIL_CONFIG_SOURCE": "env", "EMAIL_DELIVERY_MODE": "manual-link",
                "EMAIL_PUBLIC_APP_URL": os.environ["ISOLATION_SMOKE_URL"],
                "KUBERNETES_ENABLED": "true", "BACKEND_VERBOSE": "false",
            }.items()],
            "secretEnv": {"SESSION_SECRET": secrets.token_hex(32), "JWT_SECRET": secrets.token_hex(32),
                          "S3_SECRET_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}},
    "development": {"codeServer": {"version": os.environ["ISOLATION_SMOKE_VERSION"]}},
    "postgresql": {"auth": {"password": secrets.token_hex(24)}},
}))
PY
# Copy the chart before resolving dependencies to leave the working tree untouched.
cp -R "$REPO_ROOT/charts/triton-control" "$RUNTIME/chart"
helm repo add argo https://argoproj.github.io/argo-helm --repository-config "$RUNTIME/repositories.yaml" --repository-cache "$RUNTIME/helm-cache"
helm dependency build "$RUNTIME/chart" --repository-config "$RUNTIME/repositories.yaml" --repository-cache "$RUNTIME/helm-cache"
helm upgrade --install triton-control "$RUNTIME/chart" -n workspace-isolation -f "$RUNTIME/values.json" --wait --timeout 5m
kubectl -n workspace-isolation port-forward --address=127.0.0.1 service/triton-control 18080:8080 > "$ARTIFACTS/control-forward.log" 2>&1 &
PIDS+=("$!")
for attempt in {1..60}; do
  kill -0 "${PIDS[0]}" 2>/dev/null || { echo "Port forward exited; see $ARTIFACTS/control-forward.log" >&2; exit 1; }
  if curl -fsS "$ISOLATION_SMOKE_URL/health" >/dev/null; then break; fi
  if [[ $attempt == 60 ]]; then echo "Port forwards failed" >&2; exit 1; fi
  sleep 1
done
"$ISOLATION_SMOKE_PYTHON" - "$RUNTIME/environment.json" <<'PY'
import json, os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    k: v for k, v in os.environ.items() if k.startswith("ISOLATION_SMOKE_") or k == "KUBECONFIG"
}))
PY
cd "$REPO_ROOT/triton-frontend"
echo "Testing per-user workspace isolation with code-server $ISOLATION_SMOKE_VERSION"
npm run test:smoke:isolation
