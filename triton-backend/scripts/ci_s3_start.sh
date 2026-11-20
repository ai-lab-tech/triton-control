#!/usr/bin/env bash
# Disposable CI storage. Credentials must be supplied by the caller.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${AWS_ACCESS_KEY_ID:?Set the test S3 access key}"
: "${AWS_SECRET_ACCESS_KEY:?Set the test S3 secret key}"
name=${1:?Usage: ci_s3_start.sh CONTAINER_NAME [PORT]}
port=${2:-9000}
image=$(cat "$SCRIPT_DIR/seaweedfs-image.txt")
docker run --detach --name "$name" --publish "127.0.0.1:$port:9000" \
  --env AWS_ACCESS_KEY_ID --env AWS_SECRET_ACCESS_KEY \
  "$image" mini -dir=/data -ip=127.0.0.1 -ip.bind=0.0.0.0 -s3.port=9000 \
  -admin.ui=false -webdav=false -s3.autoCreateBucket=false
"${CI_S3_PYTHON:-python3}" "$SCRIPT_DIR/wait_s3.py" --endpoint "http://127.0.0.1:$port"
