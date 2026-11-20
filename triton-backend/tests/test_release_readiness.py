"""Pod probe contract, including routes that cannot be distinguished by a healthy fixture."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.materialization.readiness import check


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.receipt = Path(directory.name) / "receipt.json"
        self.expected = Path(directory.name) / "expected.json"
        receipt = {"prepared_inventory_digest": "fixture"}
        self.receipt.write_text(json.dumps(receipt))
        self.expected.write_text(
            json.dumps(
                {
                    "receipt": receipt,
                    "models": [{"name": "iris", "versions": ["1", "2"]}, {"name": "forecast", "versions": ["3"]}],
                }
            )
        )
        self.paths = [
            "/v2/health/live",
            "/v2/health/ready",
            "/v2/models/iris/versions/1/ready",
            "/v2/models/iris/versions/2/ready",
            "/v2/models/forecast/versions/3/ready",
        ]

    def test_every_required_route_must_return_200_without_redirect(self):
        for failed in [*self.paths, None]:
            with self.subTest(failed=failed), patch("app.services.materialization.readiness.HTTPConnection") as connect:
                conn = connect.return_value
                conn.getresponse.side_effect = [
                    MagicMock(status=302 if path == failed else 200, read=lambda size: b"") for path in self.paths
                ]
                if failed:
                    with self.assertRaisesRegex(ValueError, "HTTP 302"):
                        check(self.expected, self.receipt)
                else:
                    check(self.expected, self.receipt)
                connect.assert_called_once_with("127.0.0.1", 8000, timeout=2)
                requests = [call.args for call in conn.request.call_args_list]
                count = self.paths.index(failed) + 1 if failed else len(self.paths)
                self.assertEqual(requests, [("GET", path) for path in self.paths[:count]])
                conn.close.assert_called_once()

    def test_wrong_or_missing_receipt_fails_before_network(self):
        with patch("app.services.materialization.readiness.HTTPConnection") as connect:
            self.receipt.write_text("{}")
            with self.assertRaisesRegex(ValueError, "receipt differs"):
                check(self.expected, self.receipt)
            self.receipt.unlink()
            with self.assertRaises(FileNotFoundError):
                check(self.expected, self.receipt)
            connect.assert_not_called()
