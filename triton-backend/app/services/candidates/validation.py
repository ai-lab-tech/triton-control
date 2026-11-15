"""Triton repository layout checks over captured bytes, never executable code."""

import re
from pathlib import Path

from app.exceptions import BadRequestError
from app.schemas.candidate_import import ModelInventory
from app.schemas.lineage import FileEntry, InventoryV1
from app.services.candidates.artifacts import CaptureLimits, safe_path
from app.services.triton.config import parse_triton_config_pbtxt

# Only root-level provenance sidecars are excluded. The same filename inside a
# model/version is a runtime artifact and remains part of the model identity.
METADATA_FILES = frozenset({"lineage.json", "inventory.json", "receipt.json", "import-receipt.json", "validation.json"})
METADATA_DIRECTORIES = frozenset({".triton-control"})
PLATFORMS = {
    "onnxruntime_onnx": "onnxruntime",
    "tensorrt_plan": "tensorrt",
    "pytorch_libtorch": "pytorch",
    "tensorflow_graphdef": "tensorflow",
    "tensorflow_savedmodel": "tensorflow",
}
ROOT_ARTIFACTS = frozenset({"requirements.txt", "Dockerfile", "serving.json", "README.md", ".gitignore"})
FILENAMES = {"python": "model.py", "onnxruntime": "model.onnx", "tensorrt": "model.plan", "pytorch": "model.pt"}


def partition(inventory: InventoryV1) -> tuple[InventoryV1, InventoryV1]:
    files: list[FileEntry] = []
    metadata: list[FileEntry] = []
    for entry in inventory.files:
        first = entry.path.split("/", 1)[0]
        target = metadata if entry.path in METADATA_FILES or first in METADATA_DIRECTORIES else files
        target.append(entry)

    def key(entry: FileEntry) -> bytes:
        return entry.path.encode("utf-8")

    return InventoryV1(files=tuple(sorted(files, key=key))), InventoryV1(files=tuple(sorted(metadata, key=key)))


def validate_layout(
    root: Path, inventory: InventoryV1, triton_version: str, expected_backends: dict[str, str], limits: CaptureLimits
) -> tuple[ModelInventory, ...]:
    paths = {entry.path for entry in inventory.files}
    if not paths:
        raise BadRequestError("Prepared Triton repository is empty")
    if any("/" not in path and path not in ROOT_ARTIFACTS for path in paths):
        raise BadRequestError("Unsupported root-level repository artifact")
    names = sorted({path.split("/", 1)[0] for path in paths if "/" in path})
    if not names:
        raise BadRequestError("Prepared repository contains no Triton models")
    if not set(expected_backends) <= set(names):
        raise BadRequestError("Expected backend references an absent model")
    result = []
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", name):
            raise BadRequestError("Invalid Triton model name")
        config_path = f"{name}/config.pbtxt"
        if config_path not in paths:
            raise BadRequestError(f"Model {name} requires config.pbtxt")
        content = (root / config_path).read_bytes()
        if len(content) > limits.max_config_bytes:
            raise BadRequestError("config.pbtxt exceeds capture limits")
        config = parse_triton_config_pbtxt(content, triton_version)
        if config.name and config.name != name:
            raise BadRequestError(f"Model {name} disagrees with its configured name")
        platform = config.platform
        backend = config.backend
        if platform and platform not in PLATFORMS:
            raise BadRequestError(f"Model {name} declares an unsupported platform")
        platform_backend = PLATFORMS.get(platform)
        if backend and platform_backend and backend != platform_backend:
            raise BadRequestError(f"Model {name} has conflicting backend/platform declarations")
        backend = backend or platform_backend
        if not backend or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", backend):
            raise BadRequestError(f"Model {name} requires an explicit backend or supported platform")
        if name in expected_backends and expected_backends[name] != backend:
            raise BadRequestError(f"Model {name} backend does not match the requested backend")
        if config.HasField("ensemble_scheduling") or config.HasField("model_repository_agents"):
            raise BadRequestError("Ensembles and repository agents require a future multi-source contract")
        model_paths = {path for path in paths if path.startswith(name + "/")}
        versions: set[str] = set()
        for path in model_paths - {config_path}:
            relative = path[len(name) + 1 :]
            parts = relative.split("/")
            # Triton's shared Python implementation can live in model.py at the
            # model root; configs and this helper remain inventoried.
            if relative == "model.py" and backend == "python":
                continue
            if len(parts) < 2 or not re.fullmatch(r"[1-9][0-9]*", parts[0]) or int(parts[0]) > 2**63 - 1:
                raise BadRequestError(f"Model {name} requires positive numeric version directories")
            versions.add(parts[0])
        if not versions:
            raise BadRequestError(f"Model {name} has no nonempty version directory")
        if config.version_policy.HasField("specific"):
            selected = {str(version) for version in config.version_policy.specific.versions}
            if not selected or not selected <= versions:
                raise BadRequestError(f"Model {name} selects absent model versions")
        filename = config.default_model_filename or FILENAMES.get(backend)
        if platform == "tensorflow_graphdef" and not filename:
            filename = "model.graphdef"
        if platform == "tensorflow_savedmodel" and not filename:
            filename = "model.savedmodel"
        if filename:
            safe_path(filename, limits)
        for version in versions:
            prefix = f"{name}/{version}/"
            directory_model = backend == "tensorflow" and any(
                path.startswith(prefix + str(filename) + "/") for path in paths
            )
            if filename and prefix + filename not in paths and not directory_model:
                raise BadRequestError(f"Model {name} version {version} is missing its declared model artifact")
        result.append(ModelInventory(name=name, backend=backend, versions=tuple(sorted(versions, key=int))))
    return tuple(result)
