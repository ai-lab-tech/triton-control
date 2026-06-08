"""Unit tests for shared TLS helper behavior."""

import ssl
import unittest
from unittest.mock import Mock, patch

from app.core.tls import create_default_context_with_extra_ca


class TlsHelperTests(unittest.TestCase):
    def test_CreateDefaultContextWithExtraCa_CertificateProvided_AppendsToDefaultContext(self):
        # Arrange
        context = Mock()
        certificate = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"

        # Act
        with patch("app.core.tls.ssl.create_default_context", return_value=context) as create_context:
            result = create_default_context_with_extra_ca(certificate, "OIDC")

        # Assert
        self.assertIs(result, context)
        create_context.assert_called_once_with()
        context.load_verify_locations.assert_called_once_with(cadata=certificate)

    def test_CreateDefaultContextWithExtraCa_InvalidCertificate_RaisesDomainMessage(self):
        # Arrange
        context = Mock()
        context.load_verify_locations.side_effect = ssl.SSLError("bad")

        # Act / Assert
        with patch("app.core.tls.ssl.create_default_context", return_value=context):
            with self.assertRaisesRegex(RuntimeError, "Triton CA certificate is not a valid PEM certificate"):
                create_default_context_with_extra_ca("not pem", "Triton")


if __name__ == "__main__":
    unittest.main()
