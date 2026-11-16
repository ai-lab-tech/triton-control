"""Use the neutral importer providers with an explicit model/path allowlist."""

from pathlib import Path
from typing import Any

from app.exceptions import BadRequestError
from app.schemas.serving_metadata import StartupModel
from app.services.candidates.artifacts import CaptureLimits, S3Source
from app.services.snapshots.safety import allowed, reject_secrets


class SelectedModels(S3Source):
    def __init__(self, client: Any, bucket: str, prefix: str, models: tuple[StartupModel, ...]) -> None:
        super().__init__(client, bucket, prefix)
        self.models = {model.name: set(model.versions) for model in models}

    def _listing(self, limits: CaptureLimits, budget: Any) -> dict[str, dict[str, Any]]:
        listing = super()._listing(limits, budget)
        result = {}
        for path, entry in listing.items():
            parts = path.split("/")
            if parts[0] not in self.models or not allowed(path):
                continue
            if len(parts) > 1 and parts[1].isdigit() and parts[1] not in self.models[parts[0]]:
                continue
            result[path] = entry
        return result


def scan_credentials(staging: Path, paths: list[str]) -> None:
    for path in paths:
        if not allowed(path):
            raise BadRequestError("Credential paths cannot be captured")
        with (staging / path).open("rb") as stream:
            tail = b""
            while chunk := stream.read(1024**2):
                reject_secrets(tail + chunk)
                tail = chunk[-128:]
