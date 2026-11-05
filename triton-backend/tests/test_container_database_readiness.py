"""Container startup waits for SQL readiness, not just an open TCP port."""

import io
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError


class ContainerDatabaseReadinessTests(unittest.TestCase):
    def setUp(self):
        script = Path(__file__).resolve().parents[2] / "docker" / "start-triton-admin.sh"
        self.code = script.read_text().split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.engine = MagicMock()
        self.output = io.StringIO()
        for context in (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:test-only@localhost/test"}),
            patch("sqlalchemy.create_engine", return_value=self.engine),
            patch("sys.stdout", self.output),
            patch("sys.stderr", self.output),
            patch("time.sleep"),
        ):
            context.start()
            self.addCleanup(context.stop)

    def run_probe(self):
        with self.assertRaises(SystemExit) as raised:
            exec(compile(self.code, "container-database-readiness", "exec"), {})
        return raised.exception.code

    def test_retries_connection_failure_before_starting(self):
        # Arrange: a TCP proxy accepts connections but PostgreSQL is not ready.
        self.engine.connect.side_effect = [
            OperationalError(None, None, Exception("private connection details")),
            MagicMock(),
        ]
        # Act
        result = self.run_probe()
        # Assert
        self.assertEqual(result, 0)
        self.assertEqual(self.engine.connect.call_count, 2)
        self.engine.dispose.assert_called_once()
        self.assertNotIn("private connection details", self.output.getvalue())

    def test_requires_successful_query(self):
        # Arrange
        execute = self.engine.connect.return_value.__enter__.return_value.execute
        execute.side_effect = [OperationalError(None, None, Exception("starting")), None]
        # Act
        result = self.run_probe()
        # Assert
        self.assertEqual(result, 0)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(str(execute.call_args.args[0]), "SELECT 1")

    def test_exits_unsuccessfully_when_database_never_becomes_ready(self):
        # Arrange
        self.engine.connect.side_effect = OperationalError(None, None, Exception("test-only"))
        with patch("time.monotonic", side_effect=[0, 0, 61]):
            # Act
            result = self.run_probe()
        # Assert
        self.assertEqual(result, 1)
        self.engine.dispose.assert_called_once()
        self.assertNotIn("test-only", self.output.getvalue())

    def test_skips_probe_without_database_url(self):
        # Arrange
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            # Act
            result = self.run_probe()
        # Assert
        self.assertEqual(result, 0)
        self.engine.connect.assert_not_called()
