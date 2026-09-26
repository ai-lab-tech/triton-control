#!/usr/bin/env bash
# Always creates an isolated cluster; never uses or deletes the caller's cluster.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
KIND=${KIND:-kind}
PLUGIN_SMOKE_PYTHON=${PLUGIN_SMOKE_PYTHON:-python3}
export PLUGIN_SMOKE_PYTHON
for tool in docker kubectl helm node npm curl "$KIND" "$PLUGIN_SMOKE_PYTHON"; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
"$PLUGIN_SMOKE_PYTHON" -c 'import boto3'
DEFAULT_VERSION=$(node -e 'const fs=require("fs"); const source=fs.readFileSync(process.argv[1],"utf8"); const match=source.match(/codeServer:\s*\n(?:\s*#[^\n]*\n)*\s*version:\s*"([^"]+)"/); if(!match) process.exit(1); process.stdout.write(match[1]);' "$REPO_ROOT/charts/triton-control/values.yaml")
export PLUGIN_SMOKE_VERSION=${PLUGIN_SMOKE_VERSION:-$DEFAULT_VERSION}
export PLUGIN_SMOKE_PHASE=${PLUGIN_SMOKE_PHASE:-baseline}
for version in "$PLUGIN_SMOKE_VERSION" "${PLUGIN_SMOKE_CANDIDATE_VERSION:-$PLUGIN_SMOKE_VERSION}"; do
  [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Expected a code-server version such as 4.125.0" >&2; exit 1; }
done
[[ $PLUGIN_SMOKE_PHASE =~ ^[a-z0-9-]+$ ]] || { echo "Invalid smoke phase" >&2; exit 1; }
export PLUGIN_SMOKE_URL=http://localhost:18080
export PLUGIN_SMOKE_S3_URL=http://127.0.0.1:19000
export PLUGIN_SMOKE_PASSWORD=$("$PLUGIN_SMOKE_PYTHON" -c 'import secrets; print("Smoke1!" + secrets.token_hex(16))')
export PLUGIN_SMOKE_S3_KEY=smoke-access
export PLUGIN_SMOKE_S3_SECRET=$("$PLUGIN_SMOKE_PYTHON" -c 'import secrets; print(secrets.token_hex(24))')
CLUSTER="plugin-smoke-$(date +%s)-$$"
export PLUGIN_SMOKE_CONTEXT="kind-$CLUSTER"
RUNTIME=$(mktemp -d)
ARTIFACTS="$REPO_ROOT/triton-frontend/test-results/code-server-runtime-$PLUGIN_SMOKE_PHASE"
mkdir -p "$ARTIFACTS"
chmod 700 "$RUNTIME"
export KUBECONFIG="$RUNTIME/kubeconfig"
PIDS=()
CLUSTER_CREATED=false
cleanup() {
  status=$?
  trap - EXIT
  if $CLUSTER_CREATED; then
    kubectl -n plugin-smoke get pods -o wide > "$ARTIFACTS/pods.txt" 2>&1 || true
    kubectl -n plugin-smoke get events --sort-by=.metadata.creationTimestamp > "$ARTIFACTS/events.txt" 2>&1 || true
    while read -r pod; do
      kubectl -n plugin-smoke logs "$pod" --all-containers --tail=300 > "$ARTIFACTS/${pod#pod/}.log" 2>&1 || true
    done < <(kubectl -n plugin-smoke get pods -o name 2>/dev/null)
    if [[ ${PLUGIN_SMOKE_KEEP_ENV:-false} == true ]]; then
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
if [[ ${PLUGIN_SMOKE_SKIP_BUILD:-false} != true ]]; then
  docker build -t triton-control:plugin-smoke "$REPO_ROOT"
fi
docker build -t plugin-smoke-workspace:local -f "$SCRIPT_DIR/workspace.Dockerfile" "$SCRIPT_DIR"
docker build -t plugin-smoke-minio:local -f "$SCRIPT_DIR/minio.Dockerfile" "$SCRIPT_DIR"
CLUSTER_CREATED=true
"$KIND" create cluster --name "$CLUSTER" --wait 120s
"$KIND" load docker-image --name "$CLUSTER" triton-control:plugin-smoke plugin-smoke-workspace:local plugin-smoke-minio:local
kubectl create namespace plugin-smoke
# Use a temporary manifest so credentials never become shell command arguments.
"$PLUGIN_SMOKE_PYTHON" - "$RUNTIME" <<'PY'
import base64, json, os, pathlib, secrets, sys
root = pathlib.Path(sys.argv[1])
(root / "minio-secret.json").write_text(json.dumps({
    "apiVersion": "v1", "kind": "Secret", "metadata": {"name": "plugin-smoke-minio"},
    "stringData": {"MINIO_ROOT_USER": os.environ["PLUGIN_SMOKE_S3_KEY"],
                   "MINIO_ROOT_PASSWORD": os.environ["PLUGIN_SMOKE_S3_SECRET"]},
}))
(root / "values.json").write_text(json.dumps({
    "fullnameOverride": "triton-control", "ingress": {"enabled": False},
    "app": {"image": {"repository": "triton-control", "tag": "plugin-smoke"},
            "resources": {"limits": {"cpu": "2", "memory": "2Gi"}},
            "env": [{"name": k, "value": v} for k, v in {
                "OIDC_ENABLED": "false", "OIDC_CONFIG_SOURCE": "env",
                "SERVER_HTTPS_ENABLED": "false", "SESSION_HTTPS_ONLY": "false",
                "KUBERNETES_ENABLED": "true", "BACKEND_VERBOSE": "false",
            }.items()],
            "secretEnv": {"SESSION_SECRET": secrets.token_hex(32), "JWT_SECRET": secrets.token_hex(32),
                          "S3_SECRET_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}},
    "development": {"codeServer": {"version": os.environ["PLUGIN_SMOKE_VERSION"]}},
    "postgresql": {"auth": {"password": secrets.token_hex(24)}},
}))
PY
kubectl -n plugin-smoke apply -f "$RUNTIME/minio-secret.json" -f "$SCRIPT_DIR/minio.yaml"
# Copy the chart before resolving dependencies to leave the working tree untouched.
cp -R "$REPO_ROOT/charts/triton-control" "$RUNTIME/chart"
helm repo add argo https://argoproj.github.io/argo-helm --repository-config "$RUNTIME/repositories.yaml" --repository-cache "$RUNTIME/helm-cache"
helm dependency build "$RUNTIME/chart" --repository-config "$RUNTIME/repositories.yaml" --repository-cache "$RUNTIME/helm-cache"
helm upgrade --install triton-control "$RUNTIME/chart" -n plugin-smoke -f "$RUNTIME/values.json" --wait --timeout 5m
kubectl -n plugin-smoke rollout status deployment/minio --timeout=180s
kubectl -n plugin-smoke port-forward service/triton-control 18080:8080 > "$ARTIFACTS/control-forward.log" 2>&1 &
PIDS+=("$!")
kubectl -n plugin-smoke port-forward service/minio 19000:9000 > "$ARTIFACTS/minio-forward.log" 2>&1 &
PIDS+=("$!")
for attempt in {1..60}; do
  if curl -fsS "$PLUGIN_SMOKE_URL/health" >/dev/null && curl -fsS "$PLUGIN_SMOKE_S3_URL/minio/health/ready" >/dev/null; then break; fi
  if [[ $attempt == 60 ]]; then echo "Port forwards failed" >&2; exit 1; fi
  sleep 1
done
"$PLUGIN_SMOKE_PYTHON" "$SCRIPT_DIR/s3.py" init
"$PLUGIN_SMOKE_PYTHON" - "$RUNTIME/environment.json" <<'PY'
import json, os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    k: v for k, v in os.environ.items() if k.startswith("PLUGIN_SMOKE_") or k == "KUBECONFIG"
}))
PY
cd "$REPO_ROOT/triton-frontend"
npm run test:smoke:plugins
if [[ -n ${PLUGIN_SMOKE_CANDIDATE_VERSION:-} ]]; then
  # Change the real startup install version in place; preserve the PVC and all settings.
  kubectl -n plugin-smoke get statefulsets -o json > "$RUNTIME/statefulsets.json"
  "$PLUGIN_SMOKE_PYTHON" - "$RUNTIME" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
items = json.loads((root / "statefulsets.json").read_text())["items"]
workspaces = [s for s in items if s["spec"]["template"]["spec"]["containers"][0]["name"] == "code-server"]
assert len(workspaces) == 1, "Expected exactly one smoke workspace"
s = workspaces[0]
args = s["spec"]["template"]["spec"]["containers"][0]["args"]
old = "--version " + os.environ["PLUGIN_SMOKE_VERSION"] + " "
new = "--version " + os.environ["PLUGIN_SMOKE_CANDIDATE_VERSION"] + " "
assert old in args[0], "Managed code-server startup version not found"
args[0] = args[0].replace(old, new, 1)
(root / "upgrade.json").write_text(json.dumps([{"op": "replace", "path": "/spec/template/spec/containers/0/args", "value": args}]))
(root / "workspace-name").write_text(s["metadata"]["name"])
PY
  workspace=$(cat "$RUNTIME/workspace-name")
  kubectl -n plugin-smoke patch statefulset "$workspace" --type=json --patch-file "$RUNTIME/upgrade.json"
  kubectl -n plugin-smoke rollout status "statefulset/$workspace" --timeout=600s
  export PLUGIN_SMOKE_VERSION=$PLUGIN_SMOKE_CANDIDATE_VERSION
  export PLUGIN_SMOKE_PHASE=upgrade
  npm run test:smoke:plugins
fi
