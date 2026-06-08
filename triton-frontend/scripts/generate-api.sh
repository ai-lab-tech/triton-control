#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_SPEC="$ROOT/openapi/triton-backend/openapi.json"
SPEC="${OPENAPI_SPEC:-$DEFAULT_SPEC}"
OUT="$ROOT/src/app/api/generated"
JAR_DIR="$ROOT/tools"
JAR="$JAR_DIR/swagger-codegen-cli.jar"
VERSION="3.0.52"
URL="https://repo1.maven.org/maven2/io/swagger/codegen/v3/swagger-codegen-cli/${VERSION}/swagger-codegen-cli-${VERSION}.jar"

export OUT

mkdir -p "$JAR_DIR"

if [[ ! -f "$SPEC" ]]; then
  echo "OpenAPI spec not found: $SPEC"
  exit 1
fi

if [[ ! -f "$JAR" ]]; then
  curl -L "$URL" -o "$JAR"
fi

rm -rf "$OUT"

java -jar "$JAR" generate \
  -i "$SPEC" \
  -l typescript-angular \
  -o "$OUT" \
  --additional-properties=ngVersion=21.1.2,providedInRoot=true,modelPropertyNaming=original

python - <<'PY'
import os
import re
from pathlib import Path

out = Path(os.environ["OUT"])

encoder = out / "encoder.ts"
if encoder.exists():
    text = encoder.read_text(encoding="utf-8")
    text = text.replace("    encodeKey(", "    override encodeKey(")
    text = text.replace("    encodeValue(", "    override encodeValue(")
    encoder.write_text(text, encoding="utf-8")

instances_service = out / "api" / "instances.service.ts"
if instances_service.exists():
    text = instances_service.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(return this\\.httpClient\\.request<any>\\('get',`\\$\\{this\\.basePath\\}/api/instances/\\$\\{encodeURIComponent\\(String\\(instance_id\\)\\)\\}/s3/content/raw`,\\s*\\{\\s*params: queryParameters,\\s*withCredentials: this\\.configuration\\.withCredentials,\\s*headers: headers,\\s*observe: observe,\\s*)(reportProgress: reportProgress\\s*\\}\\s*\\);)",
        re.MULTILINE,
    )
    text = pattern.sub(r"\\1responseType: 'blob' as 'json',\n                \\2", text)
    instances_service.write_text(text, encoding="utf-8")
PY
