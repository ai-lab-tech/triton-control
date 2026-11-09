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
from pathlib import Path
from typing import Any, BinaryIO
from unittest.mock import patch


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    with tempfile.TemporaryDirectory(prefix="mlflow-contract-") as temporary, ExitStack() as stack:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temporary) / 'backend.db'}"
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(32)

        import uvicorn
        from fastapi import FastAPI
        from sqlmodel import Session, SQLModel
        from starlette.middleware.sessions import SessionMiddleware

        from app.api import deployment_api, s3_profile_api
        from app.core.crypto import encrypt_secret
        from app.core.user_auth import issue_access_token
        from app.db.database import engine
        from app.db.entities import S3ProfileEntity, TritonInstanceEntity, UserEntity
        from app.schemas.deployment import CreateDeploymentRequest
        from app.services.deployment import deployment, examples, kubernetes, mlflow

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
            applied.append(
                {
                    key: getattr(request, key)
                    for key in (
                        "model_uri",
                        "model_name",
                        "requirements_txt",
                        "image",
                        "model_control_mode",
                    )
                }
            )
            with Session(engine) as session:
                row = session.get(TritonInstanceEntity, _instance_id)
                assert row is not None
                applied[-1]["inference_examples"] = row.inference_examples

        class ReadyTriton:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def is_ready(self) -> bool:
                return True

            async def get_repository_index(self) -> list[dict[str, str]]:
                return [{"name": "classifier", "state": "READY"}]

        stack.enter_context(patch.object(kubernetes, "apply_deployment_resources", side_effect=record_apply))
        stack.enter_context(patch.object(kubernetes, "delete_namespace", return_value="Fixture namespace removed"))
        stack.enter_context(patch.object(deployment, "is_running_in_cluster", return_value=False))
        stack.enter_context(patch.object(mlflow, "TritonService", ReadyTriton))
        stack.enter_context(patch.object(examples, "require_s3_client", return_value=FixtureS3Client(artifact_root)))

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
