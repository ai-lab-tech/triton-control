#!/usr/bin/env bash
# Build and test the installed plugin with latest MLflow or a supplied version.
set -euo pipefail

if (( $# > 1 )); then
    echo "Usage: bash smoke-latest.sh [latest|VERSION]" >&2
    exit 2
fi
mlflow_version="${1:-latest}"
if [[ "$mlflow_version" = latest ]]; then
    mlflow_requirement='mlflow>=3.14,<4'
elif [[ "$mlflow_version" =~ ^[0-9]+(\.[0-9]+){1,3}([a-zA-Z0-9.+-]*)?$ ]]; then
    mlflow_requirement="mlflow==$mlflow_version"
else
    echo "Expected latest or an MLflow version, such as 3.14.0" >&2
    exit 2
fi

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
smoke_dir="$(mktemp -d)"
trap 'rm -rf -- "$smoke_dir"' EXIT

"${PYTHON:-python3}" -m venv "$smoke_dir/venv"
smoke_python="$smoke_dir/venv/bin/python"
"$smoke_python" -m pip install --upgrade pip build
"$smoke_python" -m build "$plugin_dir" --outdir "$smoke_dir/dist"
"$smoke_python" -m pip install --upgrade "$smoke_dir"/dist/*.whl "$mlflow_requirement"
"$smoke_python" -m pip check
"$smoke_python" -c 'import mlflow; print("Testing MLflow", mlflow.__version__)'
export MLFLOW_DISABLE_AGENT_HINT=1
"$smoke_dir/venv/bin/mlflow" deployments help -t triton-control
cd "$smoke_dir"
"$smoke_python" -m unittest discover -s "$plugin_dir/tests" -v
