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
    async def test_exception_details_never_reach_browser_or_logs(self):
        # Arrange
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
                        self.assertLogs(bff.logger, level="ERROR") as logs,
                    ):
                        # Act
                        result = await handler(SimpleNamespace(session={}), Mock())
                    # Assert
                    self.assertEqual(result.status_code, status)
                    self.assertNotIn(secret, result.body.decode())
                    expected_code = code or ("login_failed" if handler == bff.login else "callback_failed")
                    self.assertEqual(json.loads(result.body)["error"], expected_code)
                    self.assertEqual(len(logs.records), 1)
                    self.assertNotIn(secret, "\n".join(logs.output))
                    self.assertIsNone(logs.records[0].exc_info)
                    self.assertIsNone(logs.records[0].stack_info)


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

    async def test_empty_allowlist_makes_no_request(self):
        # Arrange
        service = TritonService("http://triton.internal:8000")
        client = SimpleNamespace(get=AsyncMock())
        self.enterContext(patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": ""}))
        self.enterContext(patch.object(service, "_get_client", return_value=client))

        for method in (service.collect_metrics_snapshot, service.collect_inference_metrics_snapshot):
            with self.subTest(method=method.__name__):
                # Act
                result = await method("http://triton.internal:8002/metrics")

                # Assert
                self.assertIn("not in TRITON_METRICS_ALLOWED_URLS", result["error"])
        client.get.assert_not_awaited()

    async def test_allowed_endpoint_does_not_follow_redirects(self):
        # Arrange
        url = "http://triton.internal:8002/metrics"
        service = TritonService("http://triton.internal:8000")
        requests = []

        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

        self.enterContext(patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": f"https://other/metrics, {url}"}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
            self.enterContext(patch.object(service, "_get_client", return_value=client))
            for method in (service.collect_metrics_snapshot, service.collect_inference_metrics_snapshot):
                with self.subTest(method=method.__name__):
                    requests.clear()

                    # Act
                    result = await method(url)

                    # Assert
                    self.assertIsNotNone(result["error"])
                    self.assertEqual(requests, [url])


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

    def test_long_malformed_label_name_finishes_promptly(self):
        # Arrange: below the size limit; exercises the former quadratic search.
        source = (
            "from app.services.triton.client import TritonService\n"
            "text = 'nv_inference_request_success{' + 'a' * 60000 + '} 1'\n"
            "for _ in range(3): TritonService._parse_inference_metric_series(text)\n"
        )

        # Act
        result = subprocess.run([sys.executable, "-c", source], timeout=5, check=False)

        # Assert
        self.assertEqual(result.returncode, 0)

    def test_malformed_labels_are_rejected_without_partial_results(self):
        # Arrange
        tokens = (
            'm{model="valid",broken}', 'm{model="valid" version="1"}',
            'm{model="first",model="second"}', 'm{model="unfinished}',
            r'm{model="invalid\q"}', 'm{model="line\nbreak"}',
            'm{model="valid"}suffix', 'm{1invalid="name"}',
        )
        for token in tokens:
            with self.subTest(token=token):
                # Act
                labels = TritonService._parse_prometheus_labels(token)

                # Assert
                self.assertEqual(labels, {})

    def test_empty_values_commas_braces_unicode_and_escaped_newlines(self):
        # Arrange
        token = r'm{model="模型,{v1}", empty="",note="a\nb",}'

        # Act
        labels = TritonService._parse_prometheus_labels(token)

        # Assert
        self.assertEqual(labels, {"model": "模型,{v1}", "empty": "", "note": "a\nb"})

    def test_label_token_byte_limit(self):
        # Arrange
        from app.services.triton.prometheus_labels import MAX_LABEL_TOKEN_BYTES
        overhead = len('m{model=""}')
        at_limit = 'm{model="' + 'x' * (MAX_LABEL_TOKEN_BYTES - overhead) + '"}'
        over_limit = at_limit.replace('x', 'é')

        # Act
        accepted = TritonService._parse_prometheus_labels(at_limit)
        rejected = TritonService._parse_prometheus_labels(over_limit)

        # Assert
        self.assertEqual(len(accepted["model"]), MAX_LABEL_TOKEN_BYTES - overhead)
        self.assertEqual(rejected, {})


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
