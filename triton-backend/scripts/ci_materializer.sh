#!/usr/bin/env bash
# An isolated registry is necessary: qualify the registry digest, not a local image ID.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
cluster="materializer-${GITHUB_RUN_ID:-$$}"
registry="${cluster}-registry"
work=$(mktemp -d)
cleanup() {
  kind delete cluster --name "$cluster" || true
  docker rm -f "$registry" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
: "${PYTHON_BASE_IMAGE:?Set a digest-pinned Python base image}"
if [[ ! "$PYTHON_BASE_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo 'Python base image must be pinned by registry digest' >&2
  exit 1
fi
docker run -d --restart=no -p 127.0.0.1:5001:5000 --name "$registry" registry:2
cat > "$work/kind.yaml" <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:5001"]
      endpoint = ["http://${registry}:5000"]
YAML
kind create cluster --name "$cluster" --config "$work/kind.yaml" --kubeconfig "$work/kubeconfig" --wait 60s
docker network connect kind "$registry"
# Current containerd also uses per-host registry config.
for node in $(kind get nodes --name "$cluster"); do
  docker exec "$node" mkdir -p /etc/containerd/certs.d/localhost:5001
  printf '[host."http://%s:5000"]\n  capabilities = ["pull", "resolve"]\n' "$registry" |
    docker exec -i "$node" sh -c 'cat > /etc/containerd/certs.d/localhost:5001/hosts.toml'
done
docker build -f triton-backend/materializer/Dockerfile --build-arg "PYTHON_BASE_IMAGE=$PYTHON_BASE_IMAGE" \
  -t localhost:5001/triton-materializer:qualification .
docker push localhost:5001/triton-materializer:qualification
MATERIALIZER_TEST_IMAGE=$(docker image inspect localhost:5001/triton-materializer:qualification --format '{{index .RepoDigests 0}}')
export MATERIALIZER_TEST_IMAGE
export MATERIALIZER_TEST_KUBECONFIG="$work/kubeconfig"
MATERIALIZER_TEST_PLATFORM="linux/$(kubectl --kubeconfig "$work/kubeconfig" get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}')"
export MATERIALIZER_TEST_PLATFORM
cd triton-backend
python scripts/export_materialization_schema.py --check
python scripts/qualify_materializer.py --output "${MATERIALIZER_EVIDENCE_DIR:-$root/materializer-evidence}"

# Exercise a complete owned evaluator Job against the qualified materializer.
docker build --build-arg "HELPER=$MATERIALIZER_TEST_IMAGE" -t localhost:5001/validation-runtime:test tests/validation_runtime
docker push localhost:5001/validation-runtime:test
VALIDATION_TEST_RUNTIME=$(docker image inspect localhost:5001/validation-runtime:test --format '{{index .RepoDigests 0}}')
export VALIDATION_TEST_RUNTIME
python -m unittest tests.test_candidate_validation_kubernetes -v
