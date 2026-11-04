"""MLflow tooling in the Development workspace's normal Python environment."""

import os
import shlex
from pathlib import Path

from packaging.version import Version

from app.schemas.development import CreateCodeServerRequest


def setup_command(request: CreateCodeServerRequest) -> str:
    """Install in user-site, without apt, and prefer it over bundled DALI packages."""
    mlflow_version = str(
        Version(request.mlflow_version or os.getenv("DEVELOPMENT_MLFLOW_VERSION", "").strip() or "3.14.0")
    )
    plugin_version = str(
        Version(
            request.mlflow_triton_control_version
            or os.getenv("DEVELOPMENT_MLFLOW_TRITON_CONTROL_VERSION", "").strip()
            or Path(__file__).with_name("mlflow-triton-control-version.txt").read_text(encoding="utf-8").strip()
        )
    )
    requirements = shlex.join(
        [f"mlflow=={mlflow_version}", f"mlflow-triton-control=={plugin_version}", "numpy<2", "ipykernel>=6,<8"]
    )
    check = shlex.quote(
        "import mlflow, mlflow_triton, mlflow_triton_control, ipykernel, numpy; "
        "from importlib.metadata import version; from packaging.version import Version; "
        f"assert Version(version('mlflow')) == Version({mlflow_version!r}); "
        f"assert Version(version('mlflow-triton-control')) == Version({plugin_version!r}); "
        "assert Version(numpy.__version__) < Version('2')"
    )
    user_site = shlex.quote("import site; assert site.ENABLE_USER_SITE; print(site.getusersitepackages())")
    return (
        f"MLFLOW_USER_SITE=$(python3 -c {user_site}) || "
        "{ echo 'Error: MLflow requires Python user-site packages to be enabled.' >&2; exit 1; }; "
        'export PYTHONPATH="$MLFLOW_USER_SITE${PYTHONPATH:+:$PYTHONPATH}"; '
        f"if ! python3 -c {check} >/dev/null 2>&1; then "
        "if ! (PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user --ignore-installed "
        "--disable-pip-version-check " + requirements + f" && python3 -c {check}); then "
        "echo 'Error: MLflow setup failed. Check package versions and PyPI access, or disable Install MLflow.' >&2; "
        "exit 1; fi; fi; "
        "if ! python3 -m ipykernel install --user --name triton-control "
        "--display-name 'Python (Triton Control)' --env PYTHONPATH \"$PYTHONPATH\"; then "
        "echo 'Error: MLflow notebook kernel setup failed.' >&2; exit 1; fi; "
    )
