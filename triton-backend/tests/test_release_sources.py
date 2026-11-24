"""Arrange–Act–Assert exercises of the registration API and startup snapshots."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core import installation_policy
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import ReleaseSourceEventEntity, UserEntity
from app.db.schema import create_tables
from app.exceptions import BadRequestError, ConflictError
from app.main import app
from app.services import release_sources

API = "/api/release-sources"
FIXTURES = Path(__file__).resolve().parents[2] / "contracts/serving/v1alpha1/fixtures"
ADMIN = {"email": "admin@example.test", "role": "admin"}


def document(name, target, provider="s3"):
    source = json.loads((FIXTURES / f"source-{provider}.json").read_text(encoding="utf-8"))
    source["metadata"]["name"] = name
    source["spec"]["allowedTargets"] = [target]
    return source


class ReleaseSourcesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        create_tables(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(email=ADMIN["email"], name="Administrator", role="admin"))
            session.add(UserEntity(email="member@example.test", name="Member", role="member"))
            session.commit()

        def sessions():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_claims] = lambda: ADMIN
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def sync(self, env):
        with patch.dict(os.environ, env, clear=True), Session(self.engine) as session:
            release_sources.sync_configuration(session)

    def events(self, name):
        with Session(self.engine) as session:
            return list(session.exec(
                select(ReleaseSourceEventEntity)
                .where(ReleaseSourceEventEntity.source_name == name)
                .order_by(ReleaseSourceEventEntity.revision)
            ))

    def test_admin_can_create_disable_retire_and_restore_with_history(self):
        # Arrange
        source = document("git-source", "model", "git")
        source["spec"].pop("enabled", None)
        source["spec"].pop("intervalSeconds", None)
        self.assertEqual(self.client.get(API).json(), [])

        # Act
        created = self.client.post(API, json=source)
        source["spec"]["enabled"] = True
        enabled = self.client.put(f"{API}/git-source", json={"source": source, "expected_revision": 1})
        source["spec"]["enabled"] = False
        disabled = self.client.put(f"{API}/git-source", json={"source": source, "expected_revision": 2})
        stale_delete = self.client.delete(f"{API}/git-source", params={"expected_revision": 2})
        deleted = self.client.delete(f"{API}/git-source", params={"expected_revision": 3})
        retired = self.client.get(f"{API}/git-source")
        restored = self.client.post(API, json=source)

        # Assert
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["source"]["spec"]["enabled"])
        self.assertEqual(created.json()["source"]["spec"]["intervalSeconds"], 86400)
        self.assertTrue(enabled.json()["source"]["spec"]["enabled"])
        self.assertFalse(disabled.json()["source"]["spec"]["enabled"])
        self.assertEqual((stale_delete.status_code, deleted.status_code, retired.status_code), (409, 204, 404))
        self.assertEqual(restored.json()["revision"], 5)
        self.assertEqual([(e.revision, e.retired) for e in self.events("git-source")],
                         [(1, False), (2, False), (3, False), (4, True), (5, False)])

    def test_config_is_read_only_and_reserves_id_after_removal(self):
        # Arrange
        source = document("configured", "target")
        env = {release_sources.CONFIG_JSON: json.dumps([source])}
        self.sync(env)

        # Act
        saved = self.client.get(f"{API}/configured").json()
        post = self.client.post(API, json=source)
        put = self.client.put(f"{API}/configured", json={"source": source, "expected_revision": 1})
        delete = self.client.delete(f"{API}/configured", params={"expected_revision": 1})
        self.sync(env)
        self.sync({})
        replacement = self.client.post(API, json=document("new-owner", "target"))

        # Assert
        self.assertEqual((saved["provenance"], saved["editable"]), ("environment", False))
        self.assertEqual([response.status_code for response in (post, put, delete)], [409, 409, 409])
        self.assertEqual(self.client.get(f"{API}/configured").status_code, 404)
        self.assertEqual(self.client.post(API, json=source).status_code, 409)
        self.assertEqual(replacement.status_code, 201, replacement.text)
        self.assertEqual([(e.revision, e.retired) for e in self.events("configured")], [(1, False), (2, True)])

    def test_invalid_snapshot_does_not_remove_current_sources(self):
        # Arrange
        configured = document("configured", "original")
        database = document("database", "claimed")
        self.sync({release_sources.CONFIG_JSON: json.dumps([configured])})
        self.assertEqual(self.client.post(API, json=database).status_code, 201)

        # Act / Assert
        failures = [
            ({release_sources.CONFIG_FILE: "", release_sources.CONFIG_JSON: "[]"}, BadRequestError),
            ({release_sources.CONFIG_JSON: '[{"kind":"a","kind":"b"}]'}, BadRequestError),
            ({release_sources.CONFIG_JSON: json.dumps([configured, configured])}, ConflictError),
            ({release_sources.CONFIG_JSON: json.dumps([database])}, ConflictError),
            ({release_sources.CONFIG_JSON: json.dumps([document("other", "claimed")])}, ConflictError),
            ({release_sources.CONFIG_JSON: json.dumps([configured, document("other", "original")])}, ConflictError),
        ]
        for env, error in failures:
            with self.subTest(env=env), self.assertRaises(error):
                self.sync(env)
        self.assertEqual(self.client.post(API, json=document("other", "original")).status_code, 409)
        self.assertEqual([item["revision"] for item in self.client.get(API).json()], [1, 1])

    def test_mounted_file_records_provenance_and_rejects_unknown_fields(self):
        # Arrange
        source = document("mounted", "target")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps([source]), encoding="utf-8")
            env = {release_sources.CONFIG_FILE: str(path)}

            # Act
            self.sync(env)
            source["spec"]["password"] = "redact-this"
            path.write_text(json.dumps([source]), encoding="utf-8")
            with self.assertRaises(BadRequestError) as rejected:
                self.sync(env)
            response = self.client.post(API, json=source)

        # Assert
        self.assertNotIn("redact-this", str(rejected.exception))
        self.assertNotIn("redact-this", response.text)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"{API}/mounted").json()["provenance"], "file")

    def test_database_role_and_interactive_credentials_are_required(self):
        # Arrange
        impersonated = {"email": "member@example.test", "role": "admin"}
        delegated = {**ADMIN, "token_use": "workspace"}

        # Act / Assert
        for claims in (impersonated, delegated):
            with self.subTest(claims=claims):
                app.dependency_overrides[get_claims] = lambda: claims
                self.assertEqual(self.client.get(API).status_code, 403)
                self.assertEqual(self.client.post(API, json=document("new", "target")).status_code, 403)

    def test_production_reconciles_operator_configuration_and_blocks_api_writes(self):
        source = document("configured", "original")
        self.assertEqual(self.client.post(API, json=document("database", "another")).status_code, 201)

        with patch.object(installation_policy, "mode", return_value="production"):
            self.sync({release_sources.CONFIG_JSON: json.dumps([source])})
            self.sync({release_sources.CONFIG_JSON: json.dumps([source])})
            source["spec"]["enabled"] = True
            self.sync({release_sources.CONFIG_JSON: json.dumps([source])})
            listed = self.client.get(API)
            created = self.client.post(API, json=document("new", "other"))
            updated = self.client.put(f"{API}/database", json={"source": source, "expected_revision": 1})
            deleted = self.client.delete(f"{API}/database", params={"expected_revision": 1})
            self.sync({})
            remaining = self.client.get(API)

        self.assertEqual(listed.status_code, 200)
        self.assertTrue(all(not item["editable"] for item in listed.json()))
        self.assertEqual(sorted(item["revision"] for item in listed.json()), [1, 2])
        self.assertEqual([response.status_code for response in (created, updated, deleted)], [403, 403, 403])
        self.assertEqual(len(remaining.json()), 1)
        self.assertEqual([(e.revision, e.retired) for e in self.events("configured")], [(1, False), (2, False), (3, True)])
