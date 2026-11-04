"""MLflow tooling in the Development workspace's normal Python environment."""

import os
import shlex
from pathlib import Path

from packaging.version import Version

from app.schemas.development import CreateCodeServerRequest
from app.services.development.python_packages import (
    NUMPY_REPAIR_SCRIPT,
    NUMPY_REQUIREMENT_SCRIPT,
    USER_INSTALL_FLAGS,
    hf_builder_version,
)


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
    packages = [f"mlflow=={mlflow_version}", f"mlflow-triton-control=={plugin_version}", "ipykernel>=6,<8"]
    if request.install_hf_builder:
        packages.append(f"triton-hf-builder=={hf_builder_version()}")
    requirements = shlex.join(packages)
    builder_check = (
        f"; import triton_hf_builder; assert Version(version('triton-hf-builder')) == Version({hf_builder_version()!r})"
        if request.install_hf_builder
        else ""
    )
    check = shlex.quote(
        "import mlflow, mlflow_triton, mlflow_triton_control, ipykernel, numpy; "
        "import os; from importlib.metadata import version; from packaging.version import Version; "
        "from packaging.requirements import Requirement; "
        f"assert Version(version('mlflow')) == Version({mlflow_version!r}); "
        f"assert Version(version('mlflow-triton-control')) == Version({plugin_version!r}); "
        "assert numpy.__version__ in Requirement(os.environ['MLFLOW_NUMPY_REQUIREMENT']).specifier" + builder_check
    )
    user_site = shlex.quote("import site; assert site.ENABLE_USER_SITE; print(site.getusersitepackages())")
    return (
        f"MLFLOW_USER_SITE=$(python3 -c {user_site}) || "
        "{ echo 'Error: MLflow requires Python user-site packages to be enabled.' >&2; exit 1; }; "
        'export PYTHONPATH="$MLFLOW_USER_SITE${PYTHONPATH:+:$PYTHONPATH}"; '
        f"MLFLOW_NUMPY_REQUIREMENT=$(python3 -c {shlex.quote(NUMPY_REQUIREMENT_SCRIPT)}) || "
        "{ echo 'Error: could not read the image NumPy requirements.' >&2; exit 1; }; "
        "export MLFLOW_NUMPY_REQUIREMENT; "
        f"if ! python3 -c {check} >/dev/null 2>&1; then "
        f"if ! (python3 -c {shlex.quote(NUMPY_REPAIR_SCRIPT)} && "
        f"PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install {shlex.join(USER_INSTALL_FLAGS)} "
        f'--disable-pip-version-check {requirements} "$MLFLOW_NUMPY_REQUIREMENT"'
        f" && python3 -c {check}); then "
        "echo 'Error: MLflow setup failed. Check package versions and PyPI access, or disable Install MLflow.' >&2; "
        "exit 1; fi; fi; "
        "if ! python3 -m ipykernel install --user --name triton-control "
        "--display-name 'Python (Triton Control)' --env PYTHONPATH \"$PYTHONPATH\"; then "
        "echo 'Error: MLflow notebook kernel setup failed.' >&2; exit 1; fi; "
    )
