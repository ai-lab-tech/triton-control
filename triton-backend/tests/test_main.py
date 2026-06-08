import unittest
from unittest.mock import AsyncMock, patch

from app import main


class MainAppTests(unittest.IsolatedAsyncioTestCase):
    def test_ParseHelpers_DifferentInputs_ReturnExpectedValues(self) -> None:
        self.assertTrue(main._parse_bool(" yes "))
        self.assertTrue(main._parse_bool(None, default=True))
        self.assertFalse(main._parse_bool("no"))
        self.assertEqual(main._parse_csv(" a, ,b "), ["a", "b"])
        self.assertEqual(main._parse_csv(None), [])

    async def test_RootHealthAndAuthMe_ReturnExpectedPayloads(self) -> None:
        self.assertEqual(await main.root(), {"message": "Hello from Triton Backend!"})
        self.assertEqual(await main.health_check(), {"status": "healthy"})
        self.assertEqual(
            await main.auth_me({"email": "user@example.test", "access_allowed": False}),
            {
                "authenticated": True,
                "access_allowed": False,
                "user": {"email": "user@example.test", "access_allowed": False},
            },
        )

    def test_Startup_InitializesDatabaseAndHealthRefresher(self) -> None:
        with patch("app.main.init_db") as init_db, patch.object(main.instance_health_refresher, "start") as start:
            main.on_startup()

        init_db.assert_called_once()
        start.assert_called_once()

    async def test_Shutdown_StopsBackgroundWorkersAndClients(self) -> None:
        with patch.object(main.instance_health_refresher, "stop", AsyncMock()) as stop, patch.object(
            main.TritonService,
            "close_all_clients",
            AsyncMock(),
        ) as close_all_clients:
            await main.on_shutdown()

        stop.assert_awaited_once()
        close_all_clients.assert_awaited_once()

    def test_Run_HttpAndHttpsModes_PassExpectedUvicornConfig(self) -> None:
        with patch("uvicorn.run") as uvicorn_run, patch.object(main, "server_https_enabled", False):
            main.run()

        http_config = uvicorn_run.call_args.kwargs
        self.assertEqual(http_config["app"], "app.main:app")
        self.assertEqual(http_config["port"], 8000)
        self.assertNotIn("ssl_keyfile", http_config)

        with patch("uvicorn.run") as uvicorn_run, patch.object(main, "server_https_enabled", True), patch(
            "app.main.os.getenv",
            side_effect=lambda key, default=None: {
                "TLS_KEY_FILE": "/tls/key.pem",
                "TLS_CERT_FILE": "/tls/cert.pem",
            }.get(key, default),
        ):
            main.run()

        https_config = uvicorn_run.call_args.kwargs
        self.assertEqual(https_config["ssl_keyfile"], "/tls/key.pem")
        self.assertEqual(https_config["ssl_certfile"], "/tls/cert.pem")
