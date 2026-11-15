"""Compatibility and secret protection for durable deployment inputs."""

import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

from app.core.operation_secrets import decode, encode, fingerprint


class OperationSecretsTests(unittest.TestCase):
    def test_fingerprint_preserves_existing_idempotency_records(self) -> None:
        payload = {"deployment_name": "iris", "password": "test-only-password", "nested": {"b": 2, "a": 1}}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        secret = "test-only-operation-key"
        expected = hmac.digest(hashlib.sha256(secret.encode()).digest(), canonical.encode(), "sha256").hex()
        with patch.dict("os.environ", {"OPERATION_SECRET_ENCRYPTION_KEY": secret}):
            self.assertEqual(fingerprint(payload), expected)
            self.assertEqual(fingerprint(dict(reversed(list(payload.items())))), expected)
            self.assertNotEqual(fingerprint({**payload, "password": "different-test-password"}), expected)
            self.assertEqual(decode(encode(payload)), payload)
        with patch.dict("os.environ", {"OPERATION_SECRET_ENCRYPTION_KEY": "another-test-only-key"}):
            self.assertNotEqual(fingerprint(payload), expected)
