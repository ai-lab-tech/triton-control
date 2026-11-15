"""Real PostgreSQL import behavior, concurrent publication and rollback recovery."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.database import init_db
from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.services.candidates.artifacts import DirectoryArtifactStore, DirectorySource
from app.services.candidates.importer import import_repository
from tests import test_candidate_import as fixtures

TEST_URL = os.getenv("CANDIDATE_IMPORT_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_URL, "Set CANDIDATE_IMPORT_TEST_DATABASE_URL for PostgreSQL tests")
class CandidateImportPostgresTests(fixtures.CandidateImportTests):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        fixtures.write_repository(self.source)
        self.store = DirectoryArtifactStore(self.root / "artifacts")
        self.schema = "candidate_import_" + uuid4().hex
        self.admin_engine = create_engine(TEST_URL)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(TEST_URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.seed()

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def race(self, directories):
        barrier = Barrier(len(directories))

        class SynchronizedSource(DirectorySource):
            def capture(inner, destination, limits):
                result = super().capture(destination, limits)
                barrier.wait(timeout=10)
                return result

        def worker(directory):
            with Session(self.engine) as session:
                try:
                    return import_repository(
                        session, self.engineer, fixtures.request(), SynchronizedSource(directory), self.store
                    ).candidate_digest
                except ConflictError:
                    session.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=len(directories)) as pool:
            return list(pool.map(worker, directories))

    def test_simultaneous_identical_imports_publish_one_candidate_and_one_receipt(self):
        results = self.race([self.source] * 3)
        self.assertNotIn("conflict", results)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.counts(), (1, 1))

    def test_simultaneous_different_bytes_preserve_one_immutable_winner(self):
        other = self.root / "other"
        fixtures.write_repository(other, weights=b"different fixture weights")
        results = self.race([self.source, other])
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(self.counts(), (1, 1))

    def test_failed_receipt_insert_rolls_back_candidate_and_retry_recovers(self):
        with patch(
            "app.services.candidates.importer.candidate_imports.insert", side_effect=RuntimeError("fixture failure")
        ):
            with self.assertRaises(RuntimeError):
                self.do_import()
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.do_import().candidate.origin, fixtures.request().candidate_origin)

    def test_repeated_full_upgrade_preserves_captured_candidate_and_original_users(self):
        result = self.do_import()
        self.session.rollback()
        with patch("app.db.database.engine", self.engine):
            init_db()
            init_db()
        self.assertEqual(self.do_import(), result)
        self.assertEqual(self.counts(), (1, 1))
