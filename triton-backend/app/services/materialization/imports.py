"""Adapt an authorized neutral-import receipt to immutable materialization."""

from app.schemas.candidate_import import ImportedCandidate
from app.schemas.materialization import MaterializationPlan, ObjectVersion
from app.services.materialization.normalization import MaterializationError


def from_import(imported: ImportedCandidate, materializer_image: str) -> MaterializationPlan:
    """Caller authorizes the import and resolves its local S3 binding separately."""
    location = imported.artifacts
    if location.kind != "s3":
        raise MaterializationError("Publish the captured repository to versioned S3 before initialization")
    reference = location.reference
    objects = []
    for item in reference["objects"]:
        if item["area"] != "repository":
            continue
        if item["key"] != reference["prefix"] + "/repository/" + item["path"]:
            raise MaterializationError("Import object escaped its repository prefix")
        objects.append(ObjectVersion(path=item["path"], version_id=item["version_id"]))
    return MaterializationPlan(
        materializer_image=materializer_image,
        source_inventory=imported.inventory,
        prepared_inventory=imported.inventory,
        objects=tuple(objects),
    )
