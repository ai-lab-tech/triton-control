"""Build context input, authorization, upload and durable lifecycle regression tests."""

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.build_context_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import S3ProfileEntity, TeamEntity, UserEntity, UserTeamEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import build_contexts as repo
from app.repositories import operations, teams
from app.schemas.build_contexts import BuildContextRequest
from app.schemas.build_policy import BuildPolicy
from app.services.builds import contexts, contexts_cleanup
from app.services.builds.contexts_archive import prepare, sha
from app.services.operations import OperationService, OperationWorker

IMAGE = "example.invalid/base@sha256:" + hashlib.sha256(b"synthetic base").hexdigest()


def policy():
    return BuildPolicy(
        executor_image=IMAGE, release="v1.0.0", namespace="builds", s3_profile_name="staging",
        staging_prefix="builds/", allowed_repositories=("example.invalid/result",),
        base_pull_secret="base", push_secret="push", runtime_pull_secret="runtime",
        provisioning_revision="test", egress_cidrs=("10.0.0.0/8",),
    )


def archive(files=None, special=None):
    if files is None:
        files = {"Dockerfile": f"FROM {IMAGE}\nCOPY . /app\n".encode(), "requirements.txt": b"numpy==2.2.6\n",
                 "app.py": b"print(1)\n"}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if special:
            tar.addfile(special)
    return output.getvalue()


def request(raw=None, **changes):
    raw = archive() if raw is None else raw
    return BuildContextRequest(archive_digest=sha(raw), archive_bytes=len(raw), allowed_paths=(".",),
                               dependency_files=("requirements.txt",), base_images=(IMAGE,), **changes)


class ArchiveTests(unittest.TestCase):
    def prepare(self, raw, req=None, limit=10485760):
        with tempfile.TemporaryDirectory() as temp, io.BytesIO() as out:
            result = prepare(io.BytesIO(raw), Path(temp), out, req or request(raw), limit)
            return result, out.getvalue()

    def test_filters_dockerignore_and_secret_paths_and_is_deterministic(self):
        files = {"Dockerfile": f"FROM {IMAGE}\n".encode(), "requirements.txt": b"test", "app.py": b"x",
                 ".dockerignore": b"*.log\ncache/**\n!cache/keep.txt\n.env\n!.env\n",
                 "one.log": b"log", "cache/delete.txt": b"x", "cache/keep.txt": b"keep",
                 ".env": b"PASSWORD=test", ".aws/credentials": b"fixture", "key.pem": b"fixture"}
        first, data = self.prepare(archive(files))
        second, other = self.prepare(archive(dict(reversed(list(files.items())))))
        self.assertEqual(first, second)
        self.assertEqual(data, other)
        paths = {f.path for f in first.files}
        self.assertEqual(paths, {"Dockerfile", "requirements.txt", "app.py", ".dockerignore", "cache/keep.txt"})
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            self.assertEqual(set(tar.getnames()), paths)
        self.assertEqual(first.dependencies, {"requirements.txt": sha(b"test")})

    def test_dockerfile_specific_ignore_and_allowed_paths(self):
        raw = archive({"Dockerfile": f"FROM {IMAGE}\n".encode(), "requirements.txt": b"test",
                       ".dockerignore": b"requirements.txt\n", "Dockerfile.dockerignore": b"app.py\n",
                       "app.py": b"x", "unrelated/file.txt": b"x"})
        req = request(raw).model_copy(update={"allowed_paths": ("Dockerfile", "Dockerfile.dockerignore",
                                                               "requirements.txt", "app.py")})
        result, _ = self.prepare(raw, req)
        self.assertEqual({f.path for f in result.files}, {"Dockerfile", "Dockerfile.dockerignore", "requirements.txt"})

    def test_links_devices_extensions_traversal_duplicates_rejected_even_if_ignored(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE,
                     tarfile.XHDTYPE, tarfile.GNUTYPE_SPARSE):
            info = tarfile.TarInfo("ignored")
            info.type, info.linkname = kind, "/etc/passwd"
            with self.subTest(kind=kind), self.assertRaises(BadRequestError):
                self.prepare(archive(special=info))
        for path in ("../escape", "/absolute", "a/../x", "a\\b", "Dockerfile", "dockerfile"):
            info = tarfile.TarInfo(path)
            with self.subTest(path=path), self.assertRaises(BadRequestError):
                self.prepare(archive(special=info))

    def test_missing_truncated_and_oversized_archives(self):
        raw = archive()
        for broken in (raw[:700], raw[:3072], raw[:-1024] + b"garbage" * 200):
            with self.subTest(size=len(broken)), self.assertRaises(BadRequestError):
                self.prepare(broken, request())
        with self.assertRaises(BadRequestError):
            self.prepare(raw, limit=10)
        missing = archive({"Dockerfile": f"FROM {IMAGE}\n".encode()})
        with self.assertRaises(BadRequestError):
            self.prepare(missing)

    def test_dockerfile_rejects_unpinned_images_and_inline_credentials(self):
        for text in ("FROM alpine:latest", "FROM ${BASE}", "FROM scratch", "FROM " + IMAGE + "\nENV PASSWORD=x",
                     "# syntax=docker/dockerfile:latest\nFROM " + IMAGE,
                     "FROM " + IMAGE + "\nCOPY --from=alpine:latest /file /file"):
            raw = archive({"Dockerfile": text.encode(), "requirements.txt": b"x"})
            with self.subTest(text=text), self.assertRaises(BadRequestError):
                self.prepare(raw)
    def test_dockerfile_accepts_previous_stage_reference(self):
        raw = archive({"Dockerfile": f"FROM {IMAGE} AS build\nFROM build\n".encode(), "requirements.txt": b"x"})
        self.prepare(raw)
    def test_dockerfile_accepts_scratch_without_external_images(self):
        raw = archive({"Dockerfile": b"FROM scratch\n", "requirements.txt": b"x"})
        self.prepare(raw, request(raw).model_copy(update={"base_images": ()}))

    def test_each_env_assignment_is_checked_for_credentials(self):
        for instruction in ("ENV NORMAL=1 PASSWORD=fixture", "ENV NORMAL=1 API_TOKEN=fixture",
                            'ENV NORMAL="two words" SECRET_KEY=fixture', "ENV PASSWORD fixture",
                            "ARG ACCESS_KEY=fixture"):
            raw = archive({"Dockerfile": f"FROM {IMAGE}\n{instruction}\n".encode(), "requirements.txt": b"x"})

            with self.subTest(instruction=instruction), self.assertRaisesRegex(BadRequestError, "Credential arguments"):
                self.prepare(raw)

    def test_env_values_do_not_trigger_credential_name_check(self):
        raw = archive({"Dockerfile": f'FROM {IMAGE}\nENV MESSAGE="password reset"\n'.encode(),
                       "requirements.txt": b"x"})

        manifest, _ = self.prepare(raw)

        self.assertIn("Dockerfile", {item.path for item in manifest.files})

    def test_active_ignore_cannot_be_removed_by_allowlist(self):
        raw = archive({"Dockerfile": f"FROM {IMAGE}\n".encode(), "requirements.txt": b"test",
                       ".dockerignore": b"requirements.txt\n", "Dockerfile.dockerignore": b"app.py\n",
                       "app.py": b"x"})
        req = request(raw).model_copy(update={"allowed_paths": ("Dockerfile", ".dockerignore",
                                                               "requirements.txt", "app.py")})

        with self.assertRaisesRegex(BadRequestError, "active Dockerignore"):
            self.prepare(raw, req)

    def test_dockerfile_crlf_continuations_preserve_pinned_base(self):
        raw = archive({"Dockerfile": f"FROM {IMAGE} \\\r\n AS build\r\n".encode(), "requirements.txt": b"x"})

        manifest, _ = self.prepare(raw)

        self.assertEqual(manifest.base_images, (IMAGE,))

    def test_selected_private_key_content_rejected(self):
        raw = archive({"Dockerfile": f"FROM {IMAGE}\n".encode(), "requirements.txt": b"x",
                       "innocent.txt": b"-----BEGIN RSA PRIVATE KEY-----\nsynthetic"})
        with self.assertRaises(BadRequestError):
            self.prepare(raw)

    def test_request_paths_and_strict_lengths(self):
        for changes in ({"dockerfile": "."}, {"allowed_paths": ["../x"]}, {"archive_bytes": True},
                        {"base_images": ["alpine:latest"]}, {"credentials": "secret"}):
            values = request().model_dump()
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                BuildContextRequest.model_validate(values)


class FakeStore:
    def __init__(self):
        self.versions = {}
        self.writes = 0
        self.corrupt = False
        self.lose_ack = False

    def put(self, key, stream, manifest):
        self.writes += 1
        version = str(self.writes)
        self.versions[(key, version)] = stream.read()
        if self.lose_ack:
            self.lose_ack = False
            raise OSError("synthetic uncertain write")
        return version

    def verify(self, key, version, manifest, checkpoint=lambda: None):
        checkpoint()
        data = self.versions.get((key, version))
        if self.corrupt or data is None or sha(data) != manifest.archive_digest or len(data) != manifest.archive_bytes:
            raise BadRequestError("Synthetic verification failure")

    def recover(self, key, manifest, checkpoint=lambda: None):
        for k, version in self.versions:
            if k == key:
                self.verify(key, version, manifest, checkpoint)
                return version
        return None

    def delete_prefix(self, prefix, checkpoint=lambda: None):
        checkpoint()
        self.versions = {k: v for k, v in self.versions.items() if not k[0].startswith(prefix)}


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        clock = patch("app.repositories.operations.now", side_effect=lambda _: datetime.utcnow())
        clock.start()
        self.addCleanup(clock.stop)
        self.seed()

    def seed(self):
        self.store = FakeStore()
        self.policy = policy()
        self.service = OperationService()
        self.service.register(contexts.KIND, contexts.adapter)
        self.worker = OperationWorker(self.service, lambda: Session(self.engine))
        patches = [patch.object(contexts, "service", self.service),
                   patch("app.services.builds.contexts_storage.policy", side_effect=lambda: self.policy),
                   patch("app.services.builds.contexts_storage.storage", self.storage),
                   patch.object(contexts_cleanup, "storage", self.storage)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with Session(self.engine) as session:
            session.add_all([
                UserEntity(id=1, name="Engineer", email="engineer@test", role="member"),
                UserEntity(id=2, name="Other", email="other@test", role="member"),
                UserEntity(id=3, name="Admin", email="admin@test", role="admin"),
                UserEntity(id=4, name="Viewer", email="viewer@test", role="viewer"),
                TeamEntity(id=teams.INSTALLATION_TEAM_ID, name="engineering"),
            ])
            session.commit()
            session.add(S3ProfileEntity(
                id=1, owner_user_id=3, name="staging", endpoint="http://test", bucket="contexts",
                access_key="fixture", secret_key_enc="fixture", prefix="",
            ))
            session.add_all([UserTeamEntity(team_id=teams.INSTALLATION_TEAM_ID, user_id=1),
                             UserTeamEntity(team_id=teams.INSTALLATION_TEAM_ID, user_id=2)])
            session.commit()
        self.claims = {"email": "engineer@test", "sub": "engineer@test"}

    @contextmanager
    def storage(self, session, expected):
        yield self.store

    def submit(self, req=None, key="request-1", claims=None):
        with Session(self.engine) as session:
            return contexts.submit(session, claims or self.claims, req or request(), key)

    def upload(self, context_id, raw=None):
        with Session(self.engine) as session:
            token, _, _ = contexts.begin_upload(session, self.claims, context_id)
            try:
                return contexts.finish_upload(session, self.claims, context_id, token, io.BytesIO(raw or archive()))
            finally:
                contexts.abort_upload(session, context_id, token)

    def run_worker(self, context_id):
        with Session(self.engine) as session:
            op = operations.get(session, context_id)
            op.available_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(op)
            session.commit()
        self.worker.run_once()
        with Session(self.engine) as session:
            return contexts.read(session, self.claims, context_id)

    def test_submit_upload_verify_and_replay(self):
        result = self.submit()
        self.assertEqual(result.id, self.submit().id)
        self.assertFalse(result.ready)
        self.upload(result.id)
        ready = self.run_worker(result.id)
        self.assertTrue(ready.ready)
        self.assertEqual(ready.manifest.dockerfile_digest, sha(f"FROM {IMAGE}\nCOPY . /app\n".encode()))
        self.assertEqual(self.store.writes, 1)
        with Session(self.engine) as session:
            saved = repo.receipt(session, result.id)
            self.assertIsNotNone(saved)
            self.assertNotIn("secret_key", json.dumps(saved.payload))
        with self.assertRaises(ConflictError):
            self.upload(result.id)

    def test_idempotency_conflict_and_user_scoping(self):
        first = self.submit()
        with self.assertRaises(ConflictError):
            self.submit(request().model_copy(update={"dependency_files": ()}))
        second = self.submit(claims={"email": "other@test"})
        self.assertNotEqual(first.id, second.id)
        with Session(self.engine) as session, self.assertRaises(ForbiddenError):
            contexts.read(session, {"email": "other@test"}, first.id)

    def test_roles_membership_delegation_and_mode(self):
        for claims in ({"email": "viewer@test"}, {**self.claims, "token_use": "workspace_deployment"}):
            with self.subTest(claims=claims), self.assertRaises(ForbiddenError):
                self.submit(claims=claims)
        with Session(self.engine) as session:
            member = session.get(UserTeamEntity, (teams.INSTALLATION_TEAM_ID, 1))
            member.is_active = False
            session.add(member)
            session.commit()
        with self.assertRaises(ForbiddenError):
            self.submit()
        self.submit(claims={"email": "admin@test"})

    def test_expired_idle_upload_window_is_rejected_before_worker_runs(self):
        context_id = self.submit().id
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.upload_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()

        with Session(self.engine) as session, self.assertRaisesRegex(ConflictError, "upload deadline exceeded"):
            contexts.begin_upload(session, self.claims, context_id)

        self.assertEqual(self.store.writes, 0)

    def test_explicit_retry_reopens_expired_idle_upload_window(self):
        context_id = self.submit().id
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.upload_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        self.run_worker(context_id)

        with Session(self.engine) as session:
            self.service.retry(session, self.claims, context_id)
        self.upload(context_id)
        ready = self.run_worker(context_id)

        self.assertTrue(ready.ready)
        self.assertEqual(self.store.writes, 1)

    def test_concurrent_upload_reservation_and_stale_token_fencing(self):
        context_id = self.submit().id
        with Session(self.engine) as session:
            token, _, _ = contexts.begin_upload(session, self.claims, context_id)
        with Session(self.engine) as session, self.assertRaises(ConflictError):
            contexts.begin_upload(session, self.claims, context_id)
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.upload_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        with Session(self.engine) as session:
            new_token, _, _ = contexts.begin_upload(session, self.claims, context_id)
            self.assertNotEqual(token, new_token)
            with self.assertRaises(ConflictError):
                contexts.upload_guard(session, self.claims, context_id, token)

    def test_incomplete_upload_releases_reservation_without_storage_write(self):
        context_id = self.submit().id
        with self.assertRaises(BadRequestError):
            self.upload(context_id, archive()[:700])
        self.assertEqual(self.store.writes, 0)
        self.upload(context_id)
        self.assertTrue(self.run_worker(context_id).ready)

    def test_revocation_during_upload_prevents_publication(self):
        context_id = self.submit().id
        with Session(self.engine) as session:
            token, _, _ = contexts.begin_upload(session, self.claims, context_id)
            user = session.get(UserEntity, 1)
            user.is_active = False
            session.add(user)
            session.commit()
            with self.assertRaises(ForbiddenError):
                contexts.finish_upload(session, self.claims, context_id, token, io.BytesIO(archive()))
        self.assertEqual(self.store.writes, 0)

    def test_cancel_during_upload_fences_writer_and_cleans_all_attempts(self):
        context_id = self.submit().id
        with Session(self.engine) as session:
            token, _, _ = contexts.begin_upload(session, self.claims, context_id)
            self.service.cancel(session, self.claims, context_id)
            with self.assertRaises(ConflictError):
                contexts.finish_upload(session, self.claims, context_id, token, io.BytesIO(archive()))
        result = self.run_worker(context_id)
        self.assertEqual(result.operation.state, "cancelled")
        self.assertFalse(result.ready)

    def test_restart_recovers_uncertain_s3_put_without_writing_again(self):
        context_id = self.submit().id
        self.store.lose_ack = True
        with self.assertRaises(OSError):
            self.upload(context_id)
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.upload_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        self.worker = OperationWorker(self.service, lambda: Session(self.engine))
        self.assertTrue(self.run_worker(context_id).ready)
        self.assertEqual(self.store.writes, 1)

    def test_corrupt_staging_fails_then_explicit_retry_allows_reupload(self):
        context_id = self.submit().id
        self.upload(context_id)
        self.store.corrupt = True
        self.assertEqual(self.run_worker(context_id).operation.state, "failed")
        with Session(self.engine) as session:
            self.service.retry(session, self.claims, context_id)
        self.store.corrupt = False
        self.upload(context_id)
        self.assertTrue(self.run_worker(context_id).ready)
        self.assertEqual(self.store.writes, 2)

    def test_retention_deletes_all_attempt_versions_preserving_receipt_and_operations(self):
        context_id = self.submit().id
        self.upload(context_id)
        self.run_worker(context_id)
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            row.upload_until = None
            session.add(row)
            session.commit()
        self.assertEqual(contexts_cleanup.cleanup(lambda: Session(self.engine)), {"removed": 1, "failed": 0})
        self.assertEqual(self.store.versions, {})
        with Session(self.engine) as session:
            self.assertIsNotNone(repo.receipt(session, context_id))
            self.assertEqual(operations.get(session, context_id).state, "succeeded")
            self.assertFalse(contexts.read(session, self.claims, context_id).ready)
        self.assertEqual(contexts_cleanup.cleanup(lambda: Session(self.engine))["removed"], 0)

    def test_policy_change_blocks_upload_but_preserves_readable_receipt(self):
        context_id = self.submit().id
        self.policy = self.policy.model_copy(update={"provisioning_revision": "changed"})
        with Session(self.engine) as session:
            self.assertFalse(contexts.read(session, self.claims, context_id).ready)
            with self.assertRaises(ForbiddenError):
                contexts.begin_upload(session, self.claims, context_id)

    def test_executor_admission_requires_qualification_and_rechecks_actual_bytes(self):
        context_id = self.submit().id
        self.upload(context_id)
        self.run_worker(context_id)
        with Session(self.engine) as session:
            with self.assertRaises(ForbiddenError):
                contexts.consume_for_build(session, self.claims, context_id, "fixture-revision")
        with patch.object(contexts, "require_qualified") as qualified:
            with Session(self.engine) as session:
                receipt = contexts.consume_for_build(session, self.claims, context_id, "fixture-revision")
                self.assertEqual(receipt["dockerfile"], "Dockerfile")
                qualified.assert_called_once()
            self.store.corrupt = True
            with Session(self.engine) as session, self.assertRaises(BadRequestError):
                contexts.consume_for_build(session, self.claims, context_id, "fixture-revision")

    def test_cleanup_failure_is_retryable(self):
        context_id = self.submit().id
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            row.upload_until = None
            session.add(row)
            session.commit()
        with patch.object(self.store, "delete_prefix", side_effect=OSError("synthetic secret")):
            self.assertEqual(contexts_cleanup.cleanup(lambda: Session(self.engine)), {"removed": 0, "failed": 1})
        self.assertEqual(contexts_cleanup.cleanup(lambda: Session(self.engine)), {"removed": 1, "failed": 0})

    def test_cancel_of_uncertain_put_waits_for_writer_before_cleanup(self):
        context_id = self.submit().id
        self.store.lose_ack = True
        with self.assertRaises(OSError):
            self.upload(context_id)
        with Session(self.engine) as session:
            self.service.cancel(session, self.claims, context_id)
        self.assertEqual(self.run_worker(context_id).operation.state, "pending")
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.upload_until = datetime.utcnow() - timedelta(minutes=1)
            session.add(row)
            session.commit()
        self.assertEqual(self.run_worker(context_id).operation.state, "cancelled")
        self.assertEqual(self.store.versions, {})

    def test_http_streaming_upload_and_read(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_claims] = lambda: self.claims

        def sessions():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        with TestClient(app) as client:
            result = client.post("/api/build-contexts", json=request().model_dump(mode="json"),
                                 headers={"Idempotency-Key": "http-test"})
            self.assertEqual(result.status_code, 202, result.text)
            data = result.json()
            bad = client.put(data["upload_url"], content=b"bad", headers={"Content-Type": "application/x-tar"})
            self.assertEqual(bad.status_code, 400)
            uploaded = client.put(data["upload_url"], content=archive(), headers={"Content-Type": "application/x-tar"})
            self.assertEqual(uploaded.status_code, 202, uploaded.text)
            self.run_worker(data["id"])
            self.assertTrue(client.get("/api/build-contexts/" + data["id"]).json()["ready"])
