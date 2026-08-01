"""Compatibility coverage for the account-lifecycle lightweight migration."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.db.database import _migrate_users_account_lifecycle


class AccountLifecycleMigrationTests(unittest.TestCase):
    def test_MigrationIsIdempotentAndPreservesRollbackCompatibility(self) -> None:
        connection = MagicMock()
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        transaction.__exit__.return_value = None
        engine = MagicMock()
        engine.begin.return_value = transaction

        with patch("app.db.database.engine", engine):
            _migrate_users_account_lifecycle()
            _migrate_users_account_lifecycle()

        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertEqual(len(statements), 4)
        self.assertTrue(all("DROP " not in statement.upper() for statement in statements))
        self.assertEqual(sum("IF NOT EXISTS" in statement for statement in statements), 2)
        self.assertEqual(
            sum("password_hash IS NULL" in statement for statement in statements),
            2,
        )
