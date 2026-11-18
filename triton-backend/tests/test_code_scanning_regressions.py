"""Security regressions for the open CodeQL findings."""

import hashlib
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from authlib.integrations.base_client import OAuthError

from app.services.auth.account_lifecycle import _rate_limited
from app.services.oidc import bff
from app.services.triton.client import TritonService


class OidcErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_exception_details_never_reach_browser(self):
        settings = SimpleNamespace(
            oidc_enabled=True, issuer="https://issuer", client_id="client",
            client_secret="secret", redirect_uri="https://frontend/callback",
            config_source="env", strict_discovery_document_validation=True,
            ca_certificate="",
        )
        secret = "private-token-and-internal-host"
        cases = [
            (OAuthError(error=secret), 400, "oauth_error"),
            (httpx.ConnectError(secret), 502, "oidc_unreachable"),
            (RuntimeError(secret), 500, "config_error"),
            (ValueError(secret), 500, None),
        ]
        for handler in (bff.login, bff.auth_callback):
            for error, status, code in cases:
                with self.subTest(handler=handler.__name__, error=type(error).__name__):
                    with (
                        patch.object(bff, "get_settings", return_value=settings),
                        patch.object(bff, "canonical_login_url_for_redirect_host", return_value=None),
                        patch.object(bff, "load_server_metadata", new=AsyncMock(side_effect=error)),
                        patch.object(bff, "logger") as logger,
                    ):
                        result = await handler(SimpleNamespace(session={}), Mock())
                    self.assertEqual(result.status_code, status)
                    self.assertNotIn(secret, result.body.decode())
                    expected_code = code or ("login_failed" if handler == bff.login else "callback_failed")
                    self.assertEqual(json.loads(result.body)["error"], expected_code)
                    logger.exception.assert_called_once()


class MetricsRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_urls_make_no_request(self):
        allowed = "http://triton.internal:8002/metrics"
        candidates = [
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost:8002/metrics",
            allowed + "?target=http://localhost", allowed + "/../admin",
            "http://triton.internal:8002@evil.example/metrics",
            "http://triton.internal.evil.example:8002/metrics",
            "http://triton.internal:8000/metrics",
        ]
        service = TritonService("http://triton.internal:8000")
        client = SimpleNamespace(get=AsyncMock())
        for method in (service.collect_metrics_snapshot, service.collect_inference_metrics_snapshot):
            for candidate in candidates:
                with self.subTest(method=method.__name__, candidate=candidate):
                    with patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": allowed}), \
                            patch.object(service, "_get_client", return_value=client):
                        result = await method(candidate)
                    self.assertIn("not in TRITON_METRICS_ALLOWED_URLS", result["error"])
        client.get.assert_not_awaited()

    async def test_allowlist_is_required_and_redirects_are_not_followed(self):
        url = "http://triton.internal:8002/metrics"
        service = TritonService("http://triton.internal:8000")
        requests = []

        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
            with patch.object(service, "_get_client", return_value=client):
                for method in (service.collect_metrics_snapshot, service.collect_inference_metrics_snapshot):
                    with patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": ""}):
                        result = await method(url)
                        self.assertIn("not in TRITON_METRICS_ALLOWED_URLS", result["error"])
                    self.assertNotIn("http://169.254.169.254/", requests)
                    with patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": f"https://other/metrics, {url}"}):
                        result = await method(url)
                        self.assertIsNotNone(result["error"])
        self.assertEqual(requests, [url, url])


class LabelParserTests(unittest.TestCase):
    def test_escaped_quotes_and_backslashes(self):
        self.assertEqual(
            TritonService._parse_prometheus_labels(r'm{model="a\"b",path="a\\b",version="1"}'),
            {"model": 'a"b', "path": "a\\b", "version": "1"},
        )

    def test_unterminated_escaped_label_finishes_promptly(self):
        # A subprocess timeout prevents a reintroduced exponential regex from
        # hanging the whole test runner.
        source = (
            "from app.services.triton.client import TritonService\n"
            "text = 'm{A=\"' + '\\\\!' * 10000 + '}'\n"
            "assert TritonService._parse_prometheus_labels(text) == {}\n"
        )
        subprocess.run([sys.executable, "-c", source], check=True, timeout=5)


class RateLimitHashTests(unittest.TestCase):
    def test_bucket_hashes_identify_email_and_client_not_passwords(self):
        with patch(
            "app.services.auth.account_lifecycle.account_lifecycle.increment_rate_bucket", return_value=1,
        ) as bucket:
            self.assertFalse(_rate_limited(Mock(), "person@example.com", "192.0.2.1"))
        self.assertEqual(
            [call.args[1] for call in bucket.call_args_list],
            ["email:" + hashlib.sha256(b"person@example.com").hexdigest(),
             "client:" + hashlib.sha256(b"192.0.2.1").hexdigest()],
        )
