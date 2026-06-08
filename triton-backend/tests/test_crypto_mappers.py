"""Unit tests for cryptographic primitives and entity-to-DTO mappers.

Covers:
  Crypto stubs: ``encrypt_secret``, ``decrypt_secret``, ``hash_secret``,
                ``is_secret_set``.
  Mappers:      ``entity_to_dto`` (TritonInstance), ``user_entity_to_dto``,
                ``oidc_entity_to_dto``, ``dashboard_alert_entity_to_dto``.
"""

from datetime import datetime, timezone
import unittest

from app.core.crypto import decrypt_secret, encrypt_secret, hash_secret, is_secret_set
from app.db.entities import OidcConfigEntity, TritonInstanceEntity, UserEntity
from app.mappers import entity_to_dto, oidc_entity_to_dto, user_entity_to_dto


class CryptoTests(unittest.TestCase):
    def test_SecretEncryption_FunctionsCalled_ReturnIdentityValues(self):
        # Arrange
        secret = "abc"
        token = "token"

        # Act
        encrypted = encrypt_secret(secret)
        decrypted = decrypt_secret(token)

        # Assert
        self.assertEqual(encrypted, secret)
        self.assertEqual(decrypted, token)

    def test_HashSecret_SecretProvided_ProducesPasswordHashFormat(self):
        # Arrange
        secret = "topsecret"

        # Act
        hashed = hash_secret(secret)

        # Assert
        self.assertTrue(hashed.startswith("pbkdf2_sha256$120000$"))

    def test_IsSecretSet_DifferentInputShapes_ReturnsExpectedBoolean(self):
        # Arrange / Act
        set_result = is_secret_set("x")
        whitespace_result = is_secret_set("   ")
        none_result = is_secret_set(None)

        # Assert
        self.assertTrue(set_result)
        self.assertFalse(whitespace_result)
        self.assertFalse(none_result)


class MapperTests(unittest.TestCase):
    def test_EntityToDto_InstanceEntityWithS3Fields_MapsFieldsCorrectly(self):
        # Arrange
        entity = TritonInstanceEntity(
            id=7,
            url="http://triton",
            name="gpu-a",
            model_names=["a", "b"],
            server_metadata={"name": "srv"},
            health_live=True,
            health_ready=False,
            health_error="x",
            health_last_checked_at=datetime.now(timezone.utc),
            triton_verify_ssl=True,
            triton_ca_certificate="triton-cert",
            s3_enabled=True,
            s3_endpoint="http://minio:9000",
            s3_bucket="models",
            s3_region="eu-central-1",
            s3_prefix="my/",
            s3_access_key="ak",
            s3_secret_key_enc="encrypted-secret",
            s3_use_https=True,
            s3_verify_ssl=True,
            s3_ca_certificate="cert",
            s3_address_style="virtual",
        )

        # Act
        dto = entity_to_dto(entity)

        # Assert
        self.assertEqual(dto.id, 7)
        self.assertEqual(dto.s3.bucket, "models")
        self.assertEqual(dto.s3.address_style, "virtual")
        self.assertEqual(dto.s3.access_key, "ak")
        self.assertTrue(dto.s3.secret_configured)
        self.assertEqual(dto.s3.ca_certificate, "cert")
        self.assertTrue(dto.triton_verify_ssl)
        self.assertEqual(dto.triton_ca_certificate, "triton-cert")
        self.assertTrue(dto.s3.enabled)

    def test_UserEntityToDto_IdMissing_UsesZeroFallback(self):
        # Arrange
        entity = UserEntity(
            id=None,
            email="u@example.com",
            name="U",
            role="viewer",
            auth_provider="local",
            assigned_instances=["a"],
            is_active=True,
        )

        # Act
        dto = user_entity_to_dto(entity)

        # Assert
        self.assertEqual(dto.id, 0)
        self.assertEqual(dto.email, "u@example.com")
        self.assertEqual(dto.assigned_instances, ["a"])

    def test_OidcEntityToDto_CompleteEntityProvided_MapsAllFields(self):
        # Arrange
        entity = OidcConfigEntity(
            id=1,
            oidc_enabled=True,
            issuer="https://issuer",
            client_id="cid",
            client_secret="sec",
            redirect_uri="http://localhost/callback",
            scopes="openid",
            strict_discovery_document_validation=True,
            ca_certificate="cert",
            api_base_url="http://api",
        )

        # Act
        dto = oidc_entity_to_dto(entity)

        # Assert
        self.assertTrue(dto.oidc_enabled)
        self.assertEqual(dto.issuer, "https://issuer")
        self.assertTrue(dto.client_secret_configured)
        self.assertTrue(dto.strict_discovery_document_validation)
        self.assertEqual(dto.ca_certificate, "cert")


if __name__ == "__main__":
    unittest.main()
