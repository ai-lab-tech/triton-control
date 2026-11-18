"""Crash-safe materialization: only a fully verified directory is published."""

import fcntl
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from app.core.canonical import canonical_bytes, digest, inventory_digest
from app.schemas.materialization import MaterializationPlan, MaterializationReceipt
from app.services.candidates.artifacts import CaptureLimits, _open_directory
from app.services.materialization.budgets import check_plan
from app.services.materialization.filesystem import inventory
from app.services.materialization.normalization import MaterializationError, normalize


class Source(Protocol):
    def download(self, plan: MaterializationPlan, destination: Path, limits: CaptureLimits) -> None: ...


def receipt_for(plan: MaterializationPlan) -> MaterializationReceipt:
    return MaterializationReceipt(
        plan_digest=digest(plan.model_dump(mode="json")),
        source_inventory_digest=inventory_digest(plan.source_inventory),
        prepared_inventory_digest=inventory_digest(plan.prepared_inventory),
        normalization_digest=digest(plan.normalization.model_dump(mode="json")),
        materializer_image=plan.materializer_image,
    )


def verify_ready(root: Path, plan: MaterializationPlan, limits: CaptureLimits) -> MaterializationReceipt:
    fd = _open_directory(root)
    try:
        if set(os.listdir(fd)) != {"source", "models", "receipt.json"}:
            raise MaterializationError("Published materialization contains unexpected entries")
        opened = os.open("receipt.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(opened, "rb") as stream:
            actual = MaterializationReceipt.model_validate_json(stream.read(8193))
    finally:
        os.close(fd)
    expected = receipt_for(plan)
    if actual != expected:
        raise MaterializationError("Published receipt belongs to a different materialization")
    for name, checksum in (
        ("source", expected.source_inventory_digest),
        ("models", expected.prepared_inventory_digest),
    ):
        if inventory_digest(inventory(root / name, limits)) != checksum:
            raise MaterializationError("Published repository does not match its inventory")
    return actual


def materialize(
    plan: MaterializationPlan, source: Source, root: Path, *, expected_image: str, limits: CaptureLimits
) -> MaterializationReceipt:
    if expected_image != plan.materializer_image:
        raise MaterializationError("Configured helper digest differs from the materialization plan")
    check_plan(plan, limits)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = _open_directory(root)
    lock = os.open(".materializer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    os.close(fd)
    staging: Path | None = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ready = root / "ready"
        # An init retry revalidates the existing immutable result; it never
        # rewrites an adopted release or silently accepts a different helper.
        if ready.exists() or ready.is_symlink():
            return verify_ready(ready, plan, limits)
        for entry in root.iterdir():
            if entry.name == ".materializer.lock":
                continue
            if not entry.name.startswith(".staging-") or entry.is_symlink() or not entry.is_dir():
                raise MaterializationError("Materialization volume contains unexpected entries")
            shutil.rmtree(entry)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        raw, prepared = staging / "source", staging / "models"
        raw.mkdir()
        prepared.mkdir()
        source.download(plan, raw, limits)
        if inventory_digest(inventory(raw, limits)) != inventory_digest(plan.source_inventory):
            raise MaterializationError("Downloaded source inventory mismatch")
        normalize(raw, prepared, plan.source_inventory, plan.normalization)
        if inventory_digest(inventory(prepared, limits)) != inventory_digest(plan.prepared_inventory):
            raise MaterializationError("Prepared inventory mismatch; validation must use these exact bytes")
        receipt = receipt_for(plan)
        (staging / "receipt.json").write_bytes(canonical_bytes(receipt.model_dump(mode="json")) + b"\n")
        for path in staging.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
                path.chmod(0o444)
        for path in [p for p in staging.rglob("*") if p.is_dir()] + [staging]:
            directory = _open_directory(path)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        staging.chmod(0o755)
        os.rename(staging, ready)
        staging = None
        directory = _open_directory(root)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        os.close(lock)
