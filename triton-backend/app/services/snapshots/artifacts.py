"""Use the neutral importer providers with an explicit model/path allowlist."""

import os
from pathlib import Path
from typing import Any

from app.exceptions import BadRequestError
from app.schemas.experiment_snapshots import selected_model_path
from app.schemas.serving_metadata import StartupModel
from app.services.candidates.artifacts import S3Source
from app.services.snapshots.safety import allowed, reject_secrets


def store_settings() -> tuple[str, str]:
    profile = os.getenv("TRITON_PREPARATION_SNAPSHOT_PROFILE_ID", "").strip()
    root = os.getenv("TRITON_PREPARATION_SNAPSHOT_ROOT", "").strip()
    if not profile and not root:
        root = os.getenv("TRITON_PREPARATION_SNAPSHOT_DEFAULT_ROOT", "").strip()
    return root, profile


class SelectedModels(S3Source):
    def __init__(self, client: Any, bucket: str, prefix: str, models: tuple[StartupModel, ...],
                 *, require_versioning: bool = True) -> None:
        super().__init__(client, bucket, prefix, require_versioning=require_versioning)
        self.models = {model.name: set(model.versions) for model in models}

    def includes(self, path: str) -> bool:
        return selected_model_path(path, self.models) and allowed(path)


def scan_credentials(staging: Path, paths: list[str]) -> None:
    for path in paths:
        if not allowed(path):
            raise BadRequestError("Credential paths cannot be captured")
        with (staging / path).open("rb") as stream:
            tail = b""
            while chunk := stream.read(1024**2):
                reject_secrets(tail + chunk)
                tail = chunk[-128:]
