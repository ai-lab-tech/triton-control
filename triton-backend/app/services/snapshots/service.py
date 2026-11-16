"""Durable, fenced experiment capture and atomic immutable snapshot/task publication."""

import os
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any
from uuid import UUID

from botocore.config import Config
from pydantic import ValidationError
from sqlmodel import Session

from app.core.identity import require_user_entity
from app.db.entities import OperationEntity, PreparationSnapshotEntity, TritonInstanceEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.repositories import experiment_snapshots as repository
from app.repositories import operations as operation_repo
from app.repositories import s3_profiles
from app.schemas.candidate_import import ArtifactLocation
from app.schemas.experiment_snapshots import (
    PreparationAccepted,
    PreparationRequest,
    RuntimeObservation,
    SnapshotManifest,
    SnapshotReceipt,
)
from app.schemas.lineage import InventoryV1, Origin, Snapshot
from app.schemas.operations import OperationRequest, ReconcileResult
from app.schemas.preparation import TaskCreate
from app.services import preparation
from app.services.access import get_instance_or_404
from app.services.candidates.artifacts import CaptureLimits, DirectoryArtifactStore, S3ArtifactStore
from app.services.candidates.profiles import _profile_client
from app.services.development.workspaces import get_owned_code_server
from app.services.lineage import digest, inventory_digest
from app.services.operations import Action, ExecutionContext
from app.services.operations import service as operations
from app.services.snapshots.artifacts import SelectedModels, scan_credentials
from app.services.snapshots.runtime import RuntimeProvider
from app.services.snapshots.workspace import WorkspaceProvider
from app.services.storage.s3_client import S3Config, build_s3_client

KIND = "experiment.snapshot"


def development_only() -> None:
    if os.getenv("TRITON_CONTROL_MODE", "development").strip().lower() != "development":
        raise ForbiddenError("Experiment capture is available only in development")


def actor_claims(actor: UserEntity) -> dict[str, Any]:
    return {"email": actor.email, "sub": actor.email, "role": actor.role, "auth_provider": actor.auth_provider}


def _actor(session: Session, claims: dict[str, Any]) -> UserEntity:
    if claims.get("token_use"):
        raise ForbiddenError("Interactive user credentials required")
    user = require_user_entity(session, claims)
    session.refresh(user)
    if not user.is_active or user.role.strip().lower() not in {"admin", "member"}:
        raise ForbiddenError("Active member or admin required")
    return user


def _instance(session: Session, claims: dict[str, Any], instance_id: int) -> TritonInstanceEntity:
    actor = _actor(session, claims)
    # Use the current DB role, never an old admin claim.
    instance = get_instance_or_404(session, instance_id, {**claims, "role": actor.role})
    session.refresh(instance)
    return instance


def _config(instance: Any) -> dict[str, Any]:
    return {
        "instance_id": instance.id,
        "name": instance.name,
        "managed": _managed(instance),
        "namespace": instance.deployment_namespace,
        "deployment": instance.deployment_name,
        "models": sorted(instance.model_names),
        "s3_enabled": instance.s3_enabled,
        "s3_endpoint": instance.s3_endpoint,
        "s3_bucket": instance.s3_bucket,
        "s3_prefix": instance.s3_prefix,
    }


def _managed(instance: Any) -> bool:
    return bool(instance.is_self_deployed and instance.deployment_runtime == "kubernetes")


def _selectors(session: Session, claims: dict[str, Any], instance: Any, request: PreparationRequest) -> None:
    if _managed(instance):
        if not request.deployment_uid or request.expected_generation is None:
            raise BadRequestError("Managed capture requires deployment_uid and expected_generation from pod state")
        if not instance.deployment_namespace or not instance.deployment_name:
            raise ConflictError("Managed deployment identity is unavailable")
    elif request.repository is None:
        raise BadRequestError("External endpoints require explicitly supplied repository inputs")
    if request.workspace:
        get_owned_code_server(session, claims, request.workspace.workspace_id)
    if request.previous_snapshot_id:
        previous = repository.get(session, request.previous_snapshot_id)
        if previous.instance_id != instance.id or previous.actor_id != _actor(session, claims).id:
            raise ForbiddenError("Recapture must link an authorized snapshot of this experiment")


def submit(
    session: Session, claims: dict[str, Any], instance_id: int, request: PreparationRequest, key: str
) -> PreparationAccepted:
    development_only()
    instance = _instance(session, claims, instance_id)
    _selectors(session, claims, instance, request)
    operation = operations.submit(
        session,
        claims,
        OperationRequest(kind=KIND, target=str(instance_id), payload=request.model_dump(mode="json")),
        key,
    )
    return PreparationAccepted(operation=operation, snapshot_url=f"/api/preparation/snapshots/{operation.id}")


def get(session: Session, claims: dict[str, Any], snapshot_id: UUID) -> SnapshotReceipt:
    _actor(session, claims)
    row = repository.get(session, snapshot_id)
    return SnapshotReceipt(
        id=row.id,
        operation_id=row.operation_id,
        manifest_digest=row.manifest_digest,
        manifest=SnapshotManifest.model_validate(row.manifest),
        artifacts=ArtifactLocation.model_validate(row.artifacts),
        task=preparation.get_task(session, claims, row.task_id),
    )


class Heartbeat:
    def __init__(self, context: ExecutionContext) -> None:
        self.context = context
        self.stop = Event()
        self.error: BaseException | None = None
        self.thread = Thread(target=self.loop, daemon=True)

    def loop(self) -> None:
        while not self.stop.wait(5):
            try:
                self.context.checkpoint()
            except BaseException as error:
                self.error = error
                return

    def __enter__(self) -> "Heartbeat":
        self.context.checkpoint()
        self.thread.start()
        return self

    def check(self) -> None:
        if self.error:
            raise self.error
        self.context.checkpoint()

    def __exit__(self, *args: Any) -> None:
        self.stop.set()
        self.thread.join(timeout=10)
        if args[0] is None and self.error:
            raise self.error


def _store(session: Session, claims: dict[str, Any], stack: ExitStack) -> Any:
    root = os.getenv("TRITON_PREPARATION_SNAPSHOT_ROOT", "").strip()
    profile_id = os.getenv("TRITON_PREPARATION_SNAPSHOT_PROFILE_ID", "").strip()
    if bool(root) == bool(profile_id):
        raise BadRequestError("Configure exactly one controlled snapshot store (root or S3 profile)")
    if root:
        path = Path(root)
        if not path.is_absolute():
            raise BadRequestError("Snapshot store root must be an absolute persistent path")
        return DirectoryArtifactStore(path)
    try:
        selected = int(profile_id)
    except ValueError:
        raise BadRequestError("Snapshot storage profile ID is invalid") from None
    client, bucket, prefix = _checked_profile_client(session, claims, selected)
    stack.callback(client.close)
    suffix = os.getenv("TRITON_PREPARATION_SNAPSHOT_PREFIX", "preparation-snapshots")
    return S3ArtifactStore(client, bucket, "/".join(part for part in (prefix, suffix) if part))


def _source(
    session: Session, claims: dict[str, Any], instance: Any, request: PreparationRequest, stack: ExitStack
) -> Any:
    if request.repository:
        client, bucket, prefix = _checked_profile_client(session, claims, request.repository.profile_id)
        prefix = "/".join(part for part in (prefix, request.repository.prefix) if part)
    elif (
        instance.s3_enabled
        and instance.s3_bucket
        and instance.s3_endpoint
        and instance.s3_access_key
        and instance.s3_secret_key_enc
    ):
        config = S3Config(
            s3_endpoint=instance.s3_endpoint,
            s3_bucket=instance.s3_bucket,
            s3_access_key=instance.s3_access_key,
            s3_secret_key_enc=instance.s3_secret_key_enc,
            s3_use_https=bool(instance.s3_use_https),
            s3_ca_certificate=instance.s3_ca_certificate,
            s3_region=instance.s3_region,
            s3_address_style=instance.s3_address_style,
            s3_verify_ssl=True,
        )
        client = build_s3_client(
            config, client_config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1})
        )
        bucket, prefix = instance.s3_bucket, instance.s3_prefix or ""
    else:
        return None
    stack.callback(client.close)
    return SelectedModels(client, bucket, prefix, request.models)


def _observe(instance: Any, request: PreparationRequest) -> RuntimeObservation:
    if not _managed(instance):
        return RuntimeObservation(managed=False)
    observation = RuntimeProvider().observe(instance.deployment_namespace, instance.deployment_name)
    if (
        observation.deployment_uid != request.deployment_uid
        or observation.generation != request.expected_generation
        or observation.observed_generation != observation.generation
    ):
        raise ConflictError("Deployment identity/generation changed; explicitly recapture its current state")
    return observation


def _workspace(session: Session, claims: dict[str, Any], request: PreparationRequest, staging: Path) -> InventoryV1:
    if not request.workspace:
        return InventoryV1(files=())
    workspace = get_owned_code_server(session, claims, request.workspace.workspace_id)
    return WorkspaceProvider().capture(
        workspace.namespace, workspace.statefulset_name, request.workspace.files, staging
    )


def capture(operation: OperationEntity, context: ExecutionContext) -> ReconcileResult:
    request = PreparationRequest.model_validate(operation.payload)
    installation = os.getenv("TRITON_INSTALLATION_ID", "").strip()
    if not installation or len(installation) > 256:
        raise BadRequestError("Configure a stable TRITON_INSTALLATION_ID before capturing experiments")
    with Heartbeat(context) as heartbeat, ExitStack() as stack, context.sessions() as session:
        actor = require_user_entity(session, {"email": operation_actor_email(context, operation)})
        claims = actor_claims(actor)
        instance = _instance(session, claims, int(operation.target))
        _selectors(session, claims, instance, request)
        configuration = _config(instance)
        before = _observe(instance, request)
        source = _source(session, claims, instance, request, stack)
        store = _store(session, claims, stack)
        source_identity = _source_identity(source)
        store_identity = _store_identity(store)
        limits = CaptureLimits()
        if isinstance(store, S3ArtifactStore) and source and source.bucket == store.bucket:
            for model in request.models:
                model_prefix = source.prefix + model.name + "/"
                store_prefix = store.prefix + "/"
                if model_prefix.startswith(store_prefix) or store_prefix.startswith(model_prefix):
                    raise BadRequestError("Snapshot staging must not overlap selected model sources")
        gaps = {"runtime_state_unverified", "running_model_bytes_unverified"}
        if request.runtime_changes == "requires_rebuild":
            gaps.add("runtime_changes_require_build")
        if not before.managed:
            gaps.add("external_runtime_unobserved")
        if not before.images or any(not image.observed for image in before.images):
            gaps.add("running_image_identity_unknown")
        if not before.images or any(not image.platform for image in before.images):
            gaps.add("runtime_platform_unknown")
        if len({image.observed for image in before.images}) > 1:
            gaps.add("runtime_images_differ")
        if set(model.name for model in request.models) != set(instance.model_names):
            gaps.add("intended_models_differ")
        if request.workspace:
            gaps.add("workspace_changes_not_tested")
        if not request.expected_behavior:
            gaps.add("expected_behavior")
        if not request.acceptance_criteria:
            gaps.add("acceptance_criteria")
        if not request.evaluation_files:
            gaps.add("evaluation_inputs_missing")
        with tempfile.TemporaryDirectory(prefix="experiment-snapshot-") as directory:
            staging = Path(directory)
            inventory = InventoryV1(files=())
            if source:
                captured = source.capture(staging, limits)
                inventory = captured.inventory
            workspace = _workspace(session, claims, request, staging)
            scan_credentials(staging, [entry.path for entry in inventory.files])
            for model in request.models:
                paths = {entry.path for entry in inventory.files}
                if f"{model.name}/config.pbtxt" not in paths or any(
                    not any(path.startswith(f"{model.name}/{version}/") for path in paths) for version in model.versions
                ):
                    gaps.add("model_artifacts_missing")
            available = {
                entry.path.removeprefix(".workspace/"): entry for entry in (*inventory.files, *workspace.files)
            }
            if any(
                entry.path not in available
                or (entry.size, entry.digest) != (available[entry.path].size, available[entry.path].digest)
                for entry in request.evaluation_files
            ):
                gaps.add("evaluation_files_unverified")
            heartbeat.check()
            # Re-read the full selected inventory after workspace capture. Do not
            # mix an earlier model view with a later workspace/runtime view.
            if source:
                with tempfile.TemporaryDirectory(prefix="experiment-recheck-") as second:
                    after_source = source.capture(Path(second), limits)
                if after_source != captured:
                    raise ConflictError("Model inventory changed during capture")
            if request.workspace:
                with tempfile.TemporaryDirectory(prefix="workspace-recheck-") as second:
                    if _workspace(session, claims, request, Path(second)) != workspace:
                        raise ConflictError("Selected workspace files changed during capture")
            if _observe(instance, request) != before:
                raise ConflictError("Runtime generation or pod identity changed during capture")
            if _config(_instance(session, claims, int(operation.target))) != configuration:
                raise ConflictError("Instance configuration changed during capture")
            session.expire_all()
            fresh_source = _source(session, claims, instance, request, stack)
            if _source_identity(fresh_source) != source_identity:
                raise ConflictError("Source binding changed during capture")
            if _store_identity(_store(session, claims, stack)) != store_identity:
                raise ConflictError("Controlled staging binding changed during capture")
            heartbeat.check()
            origin = Origin(installation_id=installation, record_id=operation.id)
            manifest = SnapshotManifest(
                origin=origin,
                instance_id=int(operation.target),
                instance_name=instance.name,
                submitter_user_id=operation.actor_id,
                captured_at=datetime.utcnow(),
                previous_snapshot_id=request.previous_snapshot_id,
                request=request,
                runtime=before,
                source_receipt={**captured.receipt, **source_identity} if source else {},
                configuration_digest=digest(configuration),
                source_snapshot=Snapshot(
                    origin=origin,
                    kind="manual",
                    inventory=inventory_digest(InventoryV1(files=(*inventory.files, *workspace.files))),
                ),
                inventory=inventory,
                workspace_inventory=workspace,
                gaps=tuple(sorted(gaps)),
                workspace_relation="selected_unverified_changes" if request.workspace else "not_selected",
            )
            location = store.publish(staging, inventory, workspace, manifest.model_dump(mode="json"), limits)
            heartbeat.check()
            # Serialize the final fence and task/snapshot/operation transaction.
            current = operation_repo.owned(session, operation.id, operation.worker or "", operation.generation)
            _selectors(session, claims, _instance(session, claims, int(operation.target)), request)
            if current.cancel_requested:
                raise ConflictError("Capture was cancelled before publication")
            task = preparation.create_task(
                session,
                claims,
                TaskCreate(title=request.title, source_snapshot_id=UUID(operation.id), missing_inputs=sorted(gaps)),
                commit=False,
            )
            repository.add(
                session,
                PreparationSnapshotEntity(
                    id=UUID(operation.id),
                    operation_id=operation.id,
                    instance_id=int(operation.target),
                    actor_id=operation.actor_id,
                    task_id=task.id,
                    manifest_digest=digest(manifest.model_dump(mode="json")),
                    manifest=manifest.model_dump(mode="json"),
                    artifacts=location.model_dump(mode="json"),
                ),
            )
            current.state = "succeeded"
            current.result = {"snapshot_id": operation.id, "task_id": str(task.id), "gaps": sorted(gaps)}
            current.worker, current.lease_until = None, None
            session.add(current)
            session.commit()
            return ReconcileResult(state="succeeded", result=current.result)


def operation_actor_email(context: ExecutionContext, operation: OperationEntity) -> str:
    from app.repositories import users

    with context.sessions() as session:
        actor = users.find_by_id(session, operation.actor_id)
        if actor is None:
            raise ForbiddenError("Capture owner no longer exists")
        return actor.email


class SnapshotAdapter:
    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return PreparationRequest.model_validate(payload).model_dump(mode="json")
        except ValidationError:
            raise BadRequestError("Invalid experiment capture request") from None

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        development_only()
        try:
            instance_id = int(target)
        except ValueError:
            raise BadRequestError("Expected an instance ID") from None
        _instance(session, actor_claims(actor), instance_id)

    def authorize_operation(
        self, session: Session, actor: UserEntity, operation: OperationEntity, action: Action
    ) -> None:
        if actor.id != operation.actor_id and actor.role.strip().lower() != "admin":
            raise ForbiddenError("Experiment capture operation access denied")
        if action == "read" and repository.find(session, UUID(operation.id)):
            get(session, actor_claims(actor), UUID(operation.id))
            return
        self.authorize(session, actor, operation.target, action)
        if action == "execute" and not operation.cancel_requested:
            _selectors(
                session,
                actor_claims(actor),
                _instance(session, actor_claims(actor), int(operation.target)),
                PreparationRequest.model_validate(operation.payload),
            )

    def reconcile(self, operation: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        if operation.cancel_requested:
            return ReconcileResult(state="cancelled", message="Capture cancelled; development workload was unchanged")
        try:
            return capture(operation, context)
        except (BadRequestError, NotFoundError):
            return ReconcileResult(
                state="failed", message="Capture inputs or controlled staging are unavailable/unsafe"
            )
        except ConflictError:
            return ReconcileResult(
                state="failed", message="Capture changed concurrently; retry or explicitly recapture"
            )


def _source_identity(source: Any) -> dict[str, Any]:
    if source is None:
        return {}
    endpoint = getattr(getattr(getattr(source, "client", None), "meta", None), "endpoint_url", None)
    if endpoint:
        from urllib.parse import urlsplit

        url = urlsplit(endpoint)
        if url.username or url.password or url.query or url.fragment:
            raise BadRequestError("Source endpoint must be credential-free")
    return {"endpoint": endpoint, "bucket": source.bucket, "prefix": source.prefix}


def _store_identity(store: Any) -> dict[str, Any]:
    if isinstance(store, DirectoryArtifactStore):
        return {"root": str(store.root)}
    return _source_identity(store)


def _checked_profile_client(session: Session, claims: dict[str, Any], profile_id: int) -> tuple[Any, str, str]:
    profile = s3_profiles.get(session, profile_id)
    if profile is None or not profile.endpoint or not profile.access_key or not profile.secret_key_enc:
        raise BadRequestError("Selected S3 profile has no usable configured credentials")
    return _profile_client(session, claims, profile_id)
