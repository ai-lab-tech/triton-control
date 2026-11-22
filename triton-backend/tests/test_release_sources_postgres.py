"""Race independent workers against a real PostgreSQL registry."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, create_engine, select

from app.db.entities import ReleaseSourceEventEntity, UserEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.repositories import release_sources as repository
from app.schemas.serving_metadata import MetadataSource
from app.services import release_sources
from tests.test_release_sources import ADMIN, document


@unittest.skipUnless(os.getenv("RELEASE_SOURCES_TEST_DATABASE_URL"), "Set PostgreSQL test URL")
class ReleaseSourcesPostgresTests(unittest.TestCase):
    def setUp(self):
        url = os.environ["RELEASE_SOURCES_TEST_DATABASE_URL"]
        schema = "release_source_test_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def cleanup():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()

        self.addCleanup(cleanup)
        self.engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        self.addCleanup(self.engine.dispose)
        create_tables(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(email=ADMIN["email"], name="Administrator", role="admin"))
            session.commit()

    def test_one_target_owner_when_startup_and_crud_race(self):
        # Arrange
        barrier = Barrier(2)
        config = document("helm-source", "contested")
        database = MetadataSource.model_validate(document("admin-source", "contested"))

        def register(owner):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    if owner == "helm":
                        release_sources.sync_configuration(session)
                    else:
                        release_sources.save_source(session, ADMIN, database.metadata.name, database)
                except ConflictError:
                    return False
            return True

        # Act
        with patch.dict(os.environ, {release_sources.CONFIG_JSON: json.dumps([config])}, clear=True):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(register, ("helm", "admin")))

        # Assert
        self.assertCountEqual(results, [True, False])
        with Session(self.engine) as session:
            entries = repository.registrations(session)
            self.assertEqual(len(entries), 1)
            self.assertEqual(len(session.exec(select(ReleaseSourceEventEntity)).all()), 1)
            self.assertEqual(next(iter(entries.values())).document["spec"]["allowedTargets"], ["contested"])

    def test_concurrent_edits_have_one_revision_and_history_is_immutable(self):
        # Arrange
        initial = MetadataSource.model_validate(document("editable", "original"))
        with Session(self.engine) as session:
            release_sources.save_source(session, ADMIN, "editable", initial)
        barrier = Barrier(2)

        def edit(target):
            updated = MetadataSource.model_validate(document("editable", target))
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    return release_sources.save_source(session, ADMIN, "editable", updated, expected_revision=1)
                except ConflictError:
                    return None

        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ("one", "two")))
        create_tables(self.engine)

        # Assert
        self.assertCountEqual([entry.revision if entry else None for entry in results], [2, None])
        with Session(self.engine) as session:
            history = session.exec(select(ReleaseSourceEventEntity).order_by(ReleaseSourceEventEntity.revision)).all()
            self.assertEqual([row.revision for row in history], [1, 2])
            with self.assertRaises(DBAPIError):
                session.execute(text("DELETE FROM release_source_events WHERE revision = 1"))
