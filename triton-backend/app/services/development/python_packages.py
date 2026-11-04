"""Shared installation settings for workspace Python tools."""

import os
from pathlib import Path

from packaging.version import InvalidVersion, Version

USER_INSTALL_FLAGS = ("--user", "--upgrade", "--force-reinstall")
# Runs inside the chosen workspace image, before workspace tool dependencies are installed.
# pip's vendored parser is available even in images without standalone packaging.
NUMPY_REQUIREMENT_SCRIPT = """
from importlib.metadata import PackageNotFoundError, requires
from pip._vendor.packaging.requirements import Requirement
try:
    dependencies = requires('tritonserver') or []
except PackageNotFoundError:
    dependencies = []
constraints = []
for dependency in dependencies:
    requirement = Requirement(dependency)
    if requirement.name.lower() == 'numpy' and (not requirement.marker or requirement.marker.evaluate()):
        constraints.append(str(requirement.specifier))
print('numpy' + ','.join(filter(None, constraints)))
"""

# A previous --ignore-installed downgrade can leave NumPy 2's native modules
# beside NumPy 1. pip cannot reliably remove files owned by duplicate metadata.
NUMPY_REPAIR_SCRIPT = """
import importlib.util
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path
if importlib.util.find_spec('numpy') is None:
    sys.exit(0)
probe = 'import numpy; repr(numpy.dtype("float64"))'
if importlib.util.find_spec('ml_dtypes') is not None:
    probe += '; import ml_dtypes'
if subprocess.run([sys.executable, '-c', probe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    sys.exit(0)
user_site = Path(site.getusersitepackages())
entries = [p for p in user_site.iterdir() if p.name in ('numpy', 'numpy.libs')
           or (p.name.startswith('numpy-') and p.name.endswith('.dist-info'))] if user_site.is_dir() else []
if entries:
    backup = Path(tempfile.mkdtemp(prefix='numpy-repair-', dir=user_site.parent))
    for entry in entries:
        shutil.move(str(entry), str(backup / entry.name))
    print(f'Backed up broken NumPy installation to {backup}', file=sys.stderr)
"""


def hf_builder_version() -> str:
    """Resolve the shared tested builder version for both tool installers."""
    configured_version = os.getenv("DEVELOPMENT_HF_BUILDER_VERSION", "").strip() or (
        Path(__file__).with_name("hf-builder-version.txt").read_text(encoding="utf-8").strip()
    )
    try:
        configured_version = str(Version(configured_version))
    except InvalidVersion as exc:
        raise ValueError(
            "DEVELOPMENT_HF_BUILDER_VERSION or the tested version must be a valid package version"
        ) from exc
    return configured_version
