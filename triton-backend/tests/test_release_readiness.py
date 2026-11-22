"""Pod probe contract, including routes that cannot be distinguished by a healthy fixture."""

import json
import tempfile
import unittest
from http.client import RemoteDisconnected
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

    @patch("app.services.materialization.readiness.HTTPConnection")
    def test_all_required_routes_are_checked_on_loopback(self, connect):
        # Arrange
        conn = connect.return_value
        conn.getresponse.return_value = MagicMock(status=200, read=lambda size: b"")

        # Act
        check(self.expected, self.receipt)

        # Assert
        connect.assert_called_once_with("127.0.0.1", 8000, timeout=2)
        self.assertEqual([call.args for call in conn.request.call_args_list], [("GET", path) for path in self.paths])
        conn.close.assert_called_once()

    def test_each_required_route_rejects_redirects_and_unavailable_responses(self):
        for failed in self.paths:
            for status in (302, 404, 503):
                with (
                    self.subTest(failed=failed, status=status),
                    patch("app.services.materialization.readiness.HTTPConnection") as connect,
                ):
                    # Arrange
                    conn = connect.return_value
                    conn.getresponse.side_effect = [
                        MagicMock(status=status if path == failed else 200, read=lambda size: b"")
                        for path in self.paths
                    ]

                    # Act / Assert
                    with self.assertRaisesRegex(ValueError, f"HTTP {status}"):
                        check(self.expected, self.receipt)
                    count = self.paths.index(failed) + 1
                    self.assertEqual(
                        [call.args for call in conn.request.call_args_list],
                        [("GET", path) for path in self.paths[:count]],
                    )
                    conn.close.assert_called_once()

    def test_wrong_receipt_fails_before_network(self):
        with patch("app.services.materialization.readiness.HTTPConnection") as connect:
            # Arrange
            self.receipt.write_text("{}")
            # Act / Assert
            with self.assertRaisesRegex(ValueError, "receipt differs"):
                check(self.expected, self.receipt)
            connect.assert_not_called()

    def test_missing_receipt_fails_before_network(self):
        with patch("app.services.materialization.readiness.HTTPConnection") as connect:
            # Arrange
            self.receipt.unlink()
            # Act / Assert
            with self.assertRaises(FileNotFoundError):
                check(self.expected, self.receipt)
            connect.assert_not_called()

    def test_transport_failures_close_connection_and_stop_sweep(self):
        for stage, error in (
            ("request", ConnectionRefusedError()),
            ("getresponse", RemoteDisconnected("Connection closed before response")),
            ("read", TimeoutError()),
        ):
            with self.subTest(stage=stage), patch("app.services.materialization.readiness.HTTPConnection") as connect:
                # Arrange
                conn = connect.return_value
                response = conn.getresponse.return_value
                response.status = 200
                target = response if stage == "read" else conn
                getattr(target, stage).side_effect = error

                # Act / Assert
                with self.assertRaises(type(error)):
                    check(self.expected, self.receipt)
                conn.request.assert_called_once_with("GET", self.paths[0])
                conn.close.assert_called_once()

    @patch("app.services.materialization.readiness.HTTPConnection")
    def test_oversized_health_response_stops_sweep(self, connect):
        # Arrange
        conn = connect.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = b"x" * 4097

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "oversized health response"):
            check(self.expected, self.receipt)
        response.read.assert_called_once_with(4097)
        conn.request.assert_called_once_with("GET", self.paths[0])
        conn.close.assert_called_once()

    def test_pod_budget_allows_slow_successful_responses_for_every_version(self):
        # Arrange
        from app.schemas.materialization import StorageBinding
        from app.schemas.serving_metadata import StartupModel
        from app.services.materialization.kubernetes import manifests
        from tests.materialization_fixtures import LIMITS, plan, policy, serving_release

        release = serving_release()
        models = (StartupModel(name="iris", versions=tuple(str(i) for i in range(1, 11))),)
        release = release.model_copy(update={"spec": release.spec.model_copy(update={"models": models})})
        config, pod = manifests(
            release,
            plan(),
            policy(),
            StorageBinding(bucket="models", prefix="repository", secret_name="models"),
            name="iris",
            namespace="test",
            limits=LIMITS,
        )
        self.expected.write_text(config["data"]["readiness.json"])
        self.receipt.write_text(json.dumps(json.loads(config["data"]["readiness.json"])["receipt"]))
        elapsed = 0.0

        def respond():
            nonlocal elapsed
            elapsed += 1.9  # Successful responses arrive within the two-second socket timeout.
            return MagicMock(status=200, read=lambda size: b"")

        # Act
        with patch("app.services.materialization.readiness.HTTPConnection") as connect:
            connect.return_value.getresponse.side_effect = respond
            check(self.expected, self.receipt)

        # Assert
        probe = next(c for c in pod["spec"]["containers"] if c["name"] == "release-readiness")["readinessProbe"]
        self.assertEqual(connect.return_value.request.call_count, 12)
        self.assertLess(elapsed, probe["timeoutSeconds"])
