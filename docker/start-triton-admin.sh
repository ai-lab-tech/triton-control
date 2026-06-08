#!/bin/sh
set -eu

client_max_body_size="${CLIENT_MAX_BODY_SIZE:-256m}"
sed "s/__CLIENT_MAX_BODY_SIZE__/${client_max_body_size}/g" /etc/nginx/nginx.conf > /tmp/nginx.conf

python - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlparse

database_url = os.environ.get("DATABASE_URL", "")
if not database_url:
    sys.exit(0)

parsed = urlparse(database_url)
host = parsed.hostname
port = parsed.port or 5432
if not host:
    sys.exit(0)

deadline = time.monotonic() + int(os.environ.get("DATABASE_WAIT_TIMEOUT", "60"))
last_error = None

while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"Database is reachable at {host}:{port}", flush=True)
            sys.exit(0)
    except OSError as exc:
        last_error = exc
        print(f"Waiting for database at {host}:{port}: {exc}", flush=True)
        time.sleep(2)

print(f"Database did not become reachable at {host}:{port}: {last_error}", file=sys.stderr, flush=True)
sys.exit(1)
PY

log_level="${LOG_LEVEL:-}"
if [ -z "$log_level" ]; then
  case "${BACKEND_VERBOSE:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) log_level="info" ;;
    *) log_level="warning" ;;
  esac
fi

access_log_flag="--no-access-log"
case "${BACKEND_VERBOSE:-}" in
  1|true|TRUE|yes|YES|y|Y|on|ON) access_log_flag="--access-log" ;;
esac

uvicorn app.main:app --host "${BACKEND_HOST:-0.0.0.0}" --port "${BACKEND_PORT:-8000}" --log-level "$log_level" "$access_log_flag" &
backend_pid="$!"

nginx -c /tmp/nginx.conf -g "daemon off;" &
nginx_pid="$!"

term_handler() {
  kill "$backend_pid" "$nginx_pid" 2>/dev/null || true
  wait "$backend_pid" "$nginx_pid" 2>/dev/null || true
}

trap term_handler INT TERM

while true; do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    wait "$backend_pid" || exit_code="$?"
    term_handler
    exit "${exit_code:-1}"
  fi
  if ! kill -0 "$nginx_pid" 2>/dev/null; then
    wait "$nginx_pid" || exit_code="$?"
    term_handler
    exit "${exit_code:-1}"
  fi
  sleep 1
done
