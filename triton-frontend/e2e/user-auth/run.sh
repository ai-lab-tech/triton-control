#!/usr/bin/env bash
# Linux runner: host networking gives browsers and the backend the same OIDC issuer URL.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
for tool in docker python3 node npm curl; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
[[ $(uname -s) == Linux ]] || { echo "This runner requires Linux Docker host networking." >&2; exit 1; }
MODES=${AUTH_SMOKE_MODE:-all}
case "$MODES" in
  all) MODES="smtp manual-link disabled oidc" ;;
  smtp|manual-link|disabled|oidc) ;;
  *) echo "AUTH_SMOKE_MODE must be all, smtp, manual-link, disabled, or oidc" >&2; exit 1 ;;
esac
python3 - <<'PY'
import socket
for port in (18080, 18000, 15432, 18081, 19081, 18025, 11025, 19000):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
PY
RUNTIME=$(mktemp -d)
PREFIX="user-auth-smoke-$(date +%s)-$$"
ARTIFACTS="$REPO_ROOT/triton-frontend/test-results/user-auth-runtime"
mkdir -p "$ARTIFACTS"
CONTAINERS=()
MOCK_PID=""
cleanup() {
  status=$?
  trap - EXIT
  for container in "${CONTAINERS[@]}"; do
    docker logs "$container" > "$ARTIFACTS/$container.log" 2>&1 || true
    docker rm -fv "$container" >/dev/null 2>&1 || true
  done
  if [[ -n $MOCK_PID ]]; then kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; fi
  rm -rf "$RUNTIME"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$SCRIPT_DIR/prepare.py" "$RUNTIME" "$REPO_ROOT"
export AUTH_SMOKE_PASSWORD
AUTH_SMOKE_PASSWORD=$(cat "$RUNTIME/password")
export AUTH_SMOKE_URL=http://127.0.0.1:18080
export AUTH_SMOKE_MAIL_URL=http://127.0.0.1:18025
export AUTH_SMOKE_OIDC_URL=http://127.0.0.1:18081
if [[ ${AUTH_SMOKE_SKIP_BUILD:-false} != true ]]; then
  docker build -t triton-control:user-auth-smoke "$REPO_ROOT"
fi
wait_http() {
  for attempt in {1..120}; do
    if curl --max-time 2 -fsS "$1" >/dev/null 2>&1; then return; fi
    sleep 1
  done
  echo "Service did not become ready: $1" >&2
  return 1
}
MOCK_TRITON_PORT=19000 node "$REPO_ROOT/triton-frontend/e2e/mock-triton-server.mjs" > "$ARTIFACTS/mock-triton.log" 2>&1 &
MOCK_PID=$!
wait_http http://127.0.0.1:19000/v2/health/ready
mail="$PREFIX-mail"
CONTAINERS+=("$mail")
docker run -d --name "$mail" -p 127.0.0.1:18025:8025 -p 127.0.0.1:11025:1025 axllent/mailpit:v1.31.1 >/dev/null
wait_http "$AUTH_SMOKE_MAIL_URL/api/v1/messages"
if [[ " $MODES " == *" oidc "* ]]; then
  oidc="$PREFIX-oidc"
  CONTAINERS+=("$oidc")
  docker create --name "$oidc" --network host \
    -v "$RUNTIME/smoke-realm.json:/opt/keycloak/data/import/smoke-realm.json:ro" quay.io/keycloak/keycloak:26.7.4 \
    start-dev --http-host=127.0.0.1 --http-port=18081 --http-management-port=19081 \
    --hostname="$AUTH_SMOKE_OIDC_URL" --import-realm >/dev/null
  # The realm includes disposable credentials and must be readable by Keycloak's UID.
  docker start "$oidc" >/dev/null
  wait_http "$AUTH_SMOKE_OIDC_URL/realms/smoke/.well-known/openid-configuration"
fi
for mode in $MODES; do
  export AUTH_SMOKE_MODE=$mode
  curl -fsS -X DELETE "$AUTH_SMOKE_MAIL_URL/api/v1/messages" >/dev/null
  db="$PREFIX-$mode-db"
  app="$PREFIX-$mode-app"
  CONTAINERS+=("$db" "$app")
  docker run -d --name "$db" --env-file "$RUNTIME/postgres.env" \
    -p 127.0.0.1:15432:5432 postgres:16.6-alpine >/dev/null
  docker create --name "$app" --network host --env-file "$RUNTIME/$mode.env" \
    triton-control:user-auth-smoke >/dev/null
  docker cp "$RUNTIME/nginx.conf" "$app:/etc/nginx/nginx.conf"
  docker start "$app" >/dev/null
  wait_http "$AUTH_SMOKE_URL/api/auth/options"
  echo "Testing user authentication and provisioning: $mode"
  (cd "$REPO_ROOT/triton-frontend" && npm run test:smoke:auth)
  docker logs "$app" > "$ARTIFACTS/$app.log" 2>&1
  docker logs "$db" > "$ARTIFACTS/$db.log" 2>&1
  docker rm -fv "$app" "$db" >/dev/null
  # Keep only running dependencies in the exit handler; preserve completed logs.
  CONTAINERS=("${CONTAINERS[@]:0:${#CONTAINERS[@]}-2}")
done
