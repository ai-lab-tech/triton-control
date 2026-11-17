"""Fail-closed operator configuration and S3 binding construction."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.exceptions import ForbiddenError, ServiceUnavailableError
from app.services.builds import contexts_storage as provider
from tests.test_build_contexts import policy


class BuildContextSettingsTests(unittest.TestCase):
    def test_development_mode_and_explicit_policy_required(self):
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}):
            with self.assertRaises(ForbiddenError):
                provider.policy()
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "development", "TRITON_BUILD_POLICY_FILE": ""}):
            with self.assertRaises(ForbiddenError):
                provider.policy()

    def test_bounded_policy_file_uses_same_qualification_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "development", "TRITON_BUILD_POLICY_FILE": str(path)}):
                path.write_text(policy().model_dump_json())
                self.assertEqual(provider.policy().fingerprint(), policy().fingerprint())
                for raw in ("[", "{}", " " * 65537):
                    path.write_text(raw)
                    with self.assertRaises(ServiceUnavailableError):
                        provider.policy()

    def test_retention_is_configurable_and_bounded(self):
        for days, expected in (("7", 604800), ("1", 86400), ("365", 365 * 86400)):
            with patch.dict(os.environ, {"TRITON_BUILD_CONTEXT_RETENTION_DAYS": days}):
                self.assertEqual(provider.retention_seconds(), expected)
        for days in ("0", "-1", "366", "nan"):
            with patch.dict(os.environ, {"TRITON_BUILD_CONTEXT_RETENTION_DAYS": days}):
                with self.assertRaises(ServiceUnavailableError):
                    provider.retention_seconds()

    def test_scope_and_ambiguous_profile_names_fail_closed(self):
        profile = MagicMock(id=1, endpoint="https://minio.test", bucket="contexts", prefix="team/")
        with patch.object(provider.s3_profiles, "list_by_name", return_value=[profile]):
            with self.assertRaises(ForbiddenError):
                provider.binding(MagicMock(), policy())
            settings = policy().model_copy(update={"staging_prefix": "team/builds/"})
            bound = provider.binding(MagicMock(), settings)
            self.assertEqual(bound["prefix"], "team/builds/")
            self.assertNotIn("access_key", bound)
        for rows in ([], [profile, profile]):
            with patch.object(provider.s3_profiles, "list_by_name", return_value=rows):
                with self.assertRaises(ServiceUnavailableError):
                    provider.binding(MagicMock(), policy())

    def test_changed_storage_binding_rejected_without_constructing_client(self):
        expected = {"profile_id": 1, "endpoint": "https://minio.test", "bucket": "contexts", "prefix": "builds/"}
        profile = MagicMock(endpoint="https://elsewhere.test", bucket="contexts", prefix="")
        with (patch.object(provider.s3_profiles, "get", return_value=profile),
              patch.object(provider, "build_s3_client") as mk):
            with self.assertRaises(ForbiddenError):
                with provider.storage(MagicMock(), expected):
                    self.fail("Storage binding should have been rejected")
            mk.assert_not_called()

    def test_profile_client_preserves_tls_and_closes_without_exposing_credentials(self):
        expected = {"profile_id": 1, "endpoint": "https://minio.test", "bucket": "contexts", "prefix": "builds/"}
        profile = MagicMock(endpoint=expected["endpoint"], bucket="contexts", prefix="", access_key="fixture",
                            secret_key_enc="encrypted-fixture", ca_certificate="fixture-ca", region="us-east-1",
                            force_path_style=True)
        with (patch.object(provider.s3_profiles, "get", return_value=profile),
              patch.object(provider, "build_s3_client") as mk):
            with provider.storage(MagicMock(), expected):
                config = mk.call_args.args[0]
                self.assertTrue(config["s3_verify_ssl"])
                self.assertEqual(config["s3_ca_certificate"], "fixture-ca")
            mk.return_value.close.assert_called_once()
        self.assertNotIn("fixture", json.dumps(expected))

    def test_cleanup_checks_deadline_between_each_object_delete(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{
            "Versions": [{"Key": "builds/context/a", "VersionId": "1"},
                         {"Key": "builds/context/b", "VersionId": "2"}],
        }]
        checkpoint = MagicMock(side_effect=[None, None, TimeoutError("deadline")])
        store = provider.ContextStore(client, "contexts")

        with self.assertRaises(TimeoutError):
            store.delete_prefix("builds/context/", checkpoint)

        client.delete_object.assert_called_once_with(Bucket="contexts", Key="builds/context/a", VersionId="1")
