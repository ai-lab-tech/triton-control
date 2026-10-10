#!/usr/bin/env bash
set -euo pipefail

smoke_cluster="workspace-smoke-${RANDOM}"
smoke_tmp=$(mktemp -d)
export KUBECONFIG="$smoke_tmp/kubeconfig"
smoke_forward_pid=""
cleanup() {
  smoke_result=$?
  if (( smoke_result != 0 )); then
    kubectl get pods -n triton-control || true
    kubectl get events -n triton-control --sort-by=.lastTimestamp || true
    kubectl logs -n triton-control deployment/triton-control --tail=100 || true
  fi
  if [[ -n "$smoke_forward_pid" ]]; then kill "$smoke_forward_pid" 2>/dev/null || true; fi
  kind delete cluster --name "$smoke_cluster"
  rm -rf "$smoke_tmp"
}
trap cleanup EXIT

docker build -f triton-backend/scripts/workspace-smoke.Dockerfile -t triton-workspace-smoke:ci .
kind create cluster --name "$smoke_cluster" --wait 120s
kind load docker-image triton-workspace-smoke:ci --name "$smoke_cluster"
helm repo add argo https://argoproj.github.io/argo-helm
helm dependency build charts/triton-control
helm upgrade --install triton-control charts/triton-control \
  --namespace triton-control --create-namespace \
  -f triton-backend/scripts/workspace-smoke-values.yaml \
  --set-string "app.secretEnv.JWT_SECRET=$(openssl rand -hex 32)" \
  --set-string "app.secretEnv.SESSION_SECRET=$(openssl rand -hex 32)" \
  --set-string "app.secretEnv.S3_SECRET_ENCRYPTION_KEY=$(openssl rand -hex 32)" \
  --wait --timeout 10m
kubectl port-forward -n triton-control service/triton-control 18000:8000 >"$smoke_tmp/port-forward.log" 2>&1 &
smoke_forward_pid=$!
smoke_pod=$(python3 triton-backend/scripts/prepare_workspace_smoke.py)
kubectl rollout status -n triton-control "statefulset/${smoke_pod%-0}" --timeout=600s
kubectl exec -i -n triton-control "$smoke_pod" -c code-server -- python3 - \
  < triton-backend/scripts/smoke_workspace_connections.py
kubectl exec -i -n triton-control "$smoke_pod" -c code-server -- python3 - \
  < triton-backend/scripts/smoke_workspace_argo_cli.py
