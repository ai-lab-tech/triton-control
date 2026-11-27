"""Existing S3 profile access and path scope remain authoritative for imports."""

import unittest
from unittest.mock import patch

from app.core.crypto import encrypt_secret
from app.db.entities import S3ProfileEntity, UserEntity
from app.exceptions import BadRequestError, ForbiddenError
from app.services.candidates.profiles import source_from_profile, store_from_profile
from tests import test_candidate_import as fixtures
from tests.test_candidate_s3 import MemoryS3


class CandidateProfileTests(fixtures.CandidateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.session.add(
            S3ProfileEntity(
                id=1,
                owner_user_id=1,
                name="global-models",
                endpoint="https://s3.test.invalid",
                bucket="models",
                prefix="team-root",
                access_key="fixture-access",
                secret_key_enc=encrypt_secret("opaque-fixture"),
                ca_certificate="",
            )
        )
        self.session.commit()

    def test_source_resolves_global_profile_under_its_configured_prefix(self):
        with patch("app.services.candidates.profiles.build_s3_client", return_value=MemoryS3()) as factory:
            provider = source_from_profile(self.session, self.engineer, 1, "prepared/iris/")
        self.assertEqual((provider.bucket, provider.prefix), ("models", "team-root/prepared/iris/"))
        self.assertTrue(factory.call_args.args[0]["s3_verify_ssl"])
        self.assertEqual(factory.call_args.kwargs["client_config"].read_timeout, 10)

    def test_store_requires_current_admin_and_uses_the_configured_prefix(self):
        with self.assertRaises(ForbiddenError):
            store_from_profile(self.session, self.engineer, 1)
        with patch("app.services.candidates.profiles.build_s3_client", return_value=MemoryS3()):
            store = store_from_profile(self.session, self.admin, 1)
        self.assertEqual(store.prefix, "team-root/candidate-imports")
        admin = self.session.get(UserEntity, 1)
        admin.role = "member"
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            store_from_profile(self.session, self.admin, 1)

    def test_profile_prefix_escapes_are_rejected_before_building_a_client(self):
        for prefix in ("../other", "prepared//iris", "/absolute", "prepared\\iris"):
            with patch("app.services.candidates.profiles.build_s3_client") as factory:
                with self.subTest(prefix=prefix), self.assertRaises(BadRequestError):
                    source_from_profile(self.session, self.engineer, 1, prefix)
                factory.assert_not_called()

    def test_restricted_workflow_token_cannot_select_another_profile(self):
        claims = {**self.engineer, "token_use": "mlflow_workflow", "allowed_s3_profile_id": 99}
        with self.assertRaises(ForbiddenError):
            source_from_profile(self.session, claims, 1, "prepared")

    def test_stale_member_claim_does_not_bypass_current_viewer_role(self):
        user = self.session.get(UserEntity, 2)
        user.role = "viewer"
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            source_from_profile(self.session, self.engineer, 1, "prepared")
