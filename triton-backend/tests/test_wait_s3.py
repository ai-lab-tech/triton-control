"""Authenticated CI storage readiness behavior."""

import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, EndpointConnectionError

from scripts.wait_s3 import wait_for_s3


class S3ReadinessTests(unittest.TestCase):
    def test_transient_startup_failures_are_retried_until_ready(self):
        # Arrange
        client = Mock()
        client.list_buckets.side_effect = [
            EndpointConnectionError(endpoint_url="http://test.invalid"),
            ClientError(
                {
                    "ResponseMetadata": {
                        "HTTPStatusCode": 503,
                        "RequestId": "test",
                        "HostId": "test",
                        "HTTPHeaders": {},
                        "RetryAttempts": 0,
                    }
                },
                "ListBuckets",
            ),
            {"Buckets": []},
        ]

        # Act
        with patch("scripts.wait_s3.time.sleep"), patch("scripts.wait_s3.time.monotonic", return_value=0):
            wait_for_s3(client)

        # Assert
        self.assertEqual(client.list_buckets.call_count, 3)

    def test_authentication_failure_is_not_retried(self):
        # Arrange
        for status in (401, 403):
            client = Mock()
            client.list_buckets.side_effect = ClientError(
                {
                    "ResponseMetadata": {
                        "HTTPStatusCode": status,
                        "RequestId": "test",
                        "HostId": "test",
                        "HTTPHeaders": {},
                        "RetryAttempts": 0,
                    }
                },
                "ListBuckets",
            )

            # Act
            with self.subTest(status=status), patch("scripts.wait_s3.time.sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "authentication failed"):
                    wait_for_s3(client)

            # Assert
            client.list_buckets.assert_called_once()
            sleep.assert_not_called()

    def test_unavailable_service_fails_at_deadline(self):
        # Arrange
        client = Mock()
        client.list_buckets.side_effect = EndpointConnectionError(endpoint_url="http://test.invalid")

        # Act
        with patch("scripts.wait_s3.time.monotonic", side_effect=[0, 0, 60]), patch("scripts.wait_s3.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                wait_for_s3(client)

        # Assert
        client.list_buckets.assert_called_once()
