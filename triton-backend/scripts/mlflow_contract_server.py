"""Disposable backend API fixture for the published MLflow plugin compatibility job.

Uses real routes, authentication, schemas, global profiles and deployment ownership.
S3 uses a shared local directory; Kubernetes writes and Triton readiness probes
are replaced. No cluster or external object store is used.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from unittest.mock import MagicMock, patch


class FixtureS3Client:
    """Read-only S3 interface over the same files used by the published client."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int = 1000) -> dict[str, Any]:
        assert Bucket == "compatibility-models"
        return {"Contents": [
            {"Key": path.relative_to(self.root).as_posix()}
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and path.relative_to(self.root).as_posix().startswith(Prefix)
        ][:MaxKeys]}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BinaryIO]:
        from botocore.exceptions import ClientError

        assert Bucket == "compatibility-models"
        path = (self.root / Key).resolve()
        path.relative_to(self.root)
        if not path.is_file():
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": path.open("rb")}


def record_applied_deployment(
    applied: list[dict[str, Any]], instance_id: int, request: Any, session: Any,
) -> None:
    """Publish a complete fixture record for concurrent readiness requests."""
    from app.db.entities import TritonInstanceEntity

    record = {
        key: getattr(request, key)
        for key in ("model_uri", "model_name", "requirements_txt", "image", "model_control_mode")
    }
    row = session.get(TritonInstanceEntity, instance_id)
    assert row is not None
    record["inference_examples"] = row.inference_examples
    record["url"] = row.url
    applied.append(record)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    with tempfile.TemporaryDirectory(prefix="mlflow-contract-") as temporary, ExitStack() as stack:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temporary) / 'backend.db'}"
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(32)
        os.environ["OPERATION_SECRET_ENCRYPTION_KEY"] = secrets.token_urlsafe(32)

        import uvicorn
        from fastapi import FastAPI
        from sqlmodel import Session, SQLModel
        from starlette.middleware.sessions import SessionMiddleware

        from app.api import deployment_api, s3_profile_api
        from app.core.crypto import encrypt_secret
        from app.core.user_auth import issue_access_token
        from app.db.database import engine
        from app.db.entities import S3ProfileEntity, UserEntity
        from app.schemas.deployment import CreateDeploymentRequest
        from app.services.deployment import deployment, examples, kubernetes, mlflow
        from app.services.deployment import operations as durable
        from app.repositories import deployment_intents, operations
        from app.schemas.operations import ReconcileResult
        from app.services.operations import OperationService, OperationWorker
        from app.core.operation_secrets import decode

        stack.callback(engine.dispose)
        SQLModel.metadata.create_all(engine)
        tokens = []
        with Session(engine) as session:
            for user_id, profile_name in ((7, "models"), (8, "other-owner")):
                user = UserEntity(
                    id=user_id,
                    email=f"owner-{user_id}@example.test",
                    name="Compatibility owner",
                    role="member",
                    is_active=True,
                    auth_provider="local",
                    credential_version=1,
                )
                session.add(user)
                session.add(
                    S3ProfileEntity(
                        id=user_id,
                        owner_user_id=user_id,
                        name=profile_name,
                        endpoint="https://s3.example.test",
                        bucket="compatibility-models",
                        access_key="fixture-access",
                        secret_key_enc=encrypt_secret("fixture-secret"),
                    )
                )
                tokens.append(
                    issue_access_token(
                        {
                            "email": user.email,
                            "name": user.name,
                            "role": user.role,
                            "auth_provider": "local",
                            "credential_version": 1,
                        },
                        extra_claims={"token_use": "workflow_deployment"},
                    )
                )
            session.commit()

        artifact_root = Path(temporary) / "artifacts"
        artifact_root.mkdir()
        applied: list[dict[str, Any]] = []

        def record_apply(_instance_id: int, request: CreateDeploymentRequest, *_args: str) -> None:
            with Session(engine) as session:
                record_applied_deployment(applied, _instance_id, request, session)

        class ReadyTriton:
            def __init__(self, url: str, *_args: object, **_kwargs: object) -> None:
                self.url = url

            async def is_ready(self) -> bool:
                return any(item["url"] == self.url for item in applied)

            async def get_repository_index(self) -> list[dict[str, str]]:
                return [{"name": "classifier", "state": "READY"}]

        operation_service = OperationService()
        operation_service.register(deployment_intents.KIND, durable.adapter)
        stack.enter_context(patch.object(durable, "service", operation_service))
        stack.enter_context(patch.object(durable, "DeploymentProvider", return_value=MagicMock()))
        stack.enter_context(patch.object(durable, "in_cluster_namespace", return_value="fixture"))
        stack.enter_context(patch.object(operations, "now", lambda _: datetime.now(timezone.utc).replace(tzinfo=None)))
        original_reconcile = durable.adapter.reconcile

        def reconcile(operation: Any, context: Any) -> ReconcileResult:
            outcome = original_reconcile(operation, context)
            if outcome.state == "succeeded":
                with Session(engine) as session:
                    intent = deployment_intents.get(session, operation.id)
                    assert intent is not None
                    request = CreateDeploymentRequest.model_construct(**decode(intent.bundle_enc)["request"])
                    record_apply(intent.instance_id, request)
            return outcome

        def advance(_provider: Any, operation: Any, intent: Any, *_args: Any) -> ReconcileResult:
            if operation.cancel_requested:
                intent.cleaned = True
                return ReconcileResult(state="cancelled")
            return ReconcileResult(state="succeeded")

        stack.enter_context(patch.object(durable.adapter, "advance", side_effect=advance))
        stack.enter_context(patch.object(durable.adapter, "reconcile", side_effect=reconcile))
        stack.enter_context(patch.object(kubernetes, "delete_namespace", return_value="Fixture namespace removed"))
        stack.enter_context(patch.object(deployment, "is_running_in_cluster", return_value=False))
        stack.enter_context(patch.object(mlflow, "TritonService", ReadyTriton))
        stack.enter_context(patch.object(examples, "require_s3_client", return_value=FixtureS3Client(artifact_root)))

        worker = OperationWorker(operation_service, lambda: Session(engine))
        worker.start()
        stack.callback(worker.stop)

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key=secrets.token_urlsafe(32))
        app.include_router(deployment_api.router)
        app.include_router(s3_profile_api.router)

        @app.get("/contract/applied")
        def applied_requests() -> list[dict[str, Any]]:
            return applied

        listener = stack.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        connection = {
            "url": f"http://127.0.0.1:{listener.getsockname()[1]}",
            "token": tokens[0],
            "other_token": tokens[1],
            "artifact_root": str(artifact_root),
        }
        args.connection_file.write_text(json.dumps(connection), encoding="utf-8")
        args.connection_file.chmod(0o600)
        stack.callback(args.connection_file.unlink, missing_ok=True)
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[listener])


if __name__ == "__main__":
    main()
