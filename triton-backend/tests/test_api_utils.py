"""Unit tests for API utility helpers.

Covers:
  ``_normalize_triton_url`` — URL normalisation (scheme injection, trailing-
                              slash trimming, whitespace handling).
  ``extract_triton_error_detail`` — Triton HTTP error body parsing.
"""

import unittest

from app.schemas.instance import _normalize_triton_url
from app.services.triton.config import extract_triton_error_detail


class NormalizeTritonUrlTests(unittest.TestCase):
    def test_NormalizeTritonUrl_SchemeMissing_AddsHttpsScheme(self):
        # Arrange
        url = "localhost:8000"

        # Act
        normalized = _normalize_triton_url(url)

        # Assert
        self.assertEqual(normalized, "https://localhost:8000")

    def test_NormalizeTritonUrl_SchemePresentWithTrailingSlash_TrimsTrailingSlash(self):
        # Arrange
        url = "https://example.com/"

        # Act
        normalized = _normalize_triton_url(url)

        # Assert
        self.assertEqual(normalized, "https://example.com")

    def test_NormalizeTritonUrl_WhitespaceOnlyInput_ReturnsEmptyString(self):
        # Arrange
        url = "   "

        # Act
        normalized = _normalize_triton_url(url)

        # Assert
        self.assertEqual(normalized, "")


class ExtractTritonErrorDetailTests(unittest.TestCase):
    class _DummyResponse:
        def __init__(self, payload=None, text="", status_code=None):
            self._payload = payload
            self.text = text
            self.status_code = status_code

        def json(self):
            if self._payload is None:
                raise ValueError("No JSON")
            return self._payload

    class _DummyException(Exception):
        def __init__(self, response):
            super().__init__("dummy")
            self.response = response

    def test_ExtractTritonErrorDetail_ErrorFieldPresent_ReturnsErrorField(self):
        # Arrange
        exc = self._DummyException(self._DummyResponse(payload={"error": "bad request"}))

        # Act
        detail = extract_triton_error_detail(exc)

        # Assert
        self.assertEqual(detail, "bad request")

    def test_ExtractTritonErrorDetail_DetailFieldPresent_ReturnsDetailField(self):
        # Arrange
        exc = self._DummyException(self._DummyResponse(payload={"detail": "not ready"}))

        # Act
        detail = extract_triton_error_detail(exc)

        # Assert
        self.assertEqual(detail, "not ready")

    def test_ExtractTritonErrorDetail_JsonMissingUsesText_ReturnsResponseText(self):
        # Arrange
        exc = self._DummyException(self._DummyResponse(payload=None, text="plain text error"))

        # Act
        detail = extract_triton_error_detail(exc)

        # Assert
        self.assertEqual(detail, "plain text error")

    def test_ExtractTritonErrorDetail_TextMissingWithStatusCode_ReturnsStatusMessage(self):
        # Arrange
        exc = self._DummyException(self._DummyResponse(payload=None, text="", status_code=502))

        # Act
        detail = extract_triton_error_detail(exc)

        # Assert
        self.assertEqual(detail, "Triton request failed with HTTP 502")

    def test_ExtractTritonErrorDetail_NoResponseAttached_ReturnsGenericMessage(self):
        # Arrange
        exc = Exception("oops")

        # Act
        detail = extract_triton_error_detail(exc)

        # Assert
        self.assertEqual(detail, "Triton request failed")


if __name__ == "__main__":
    unittest.main()
