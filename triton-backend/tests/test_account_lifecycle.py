"""Tests for local-account lifecycle tokens, delivery, and secret handling."""

from __future__ import annotations

import os
import smtplib
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.secret_encryption import decrypt_email_secret, encrypt_email_secret
from app.db.entities import (
    AccountLifecycleTokenEntity,
    EmailConfigEntity,
    SecurityEventEntity,
    UserEntity,
)
from app.exceptions import BadRequestError
from app.repositories import account_lifecycle, users
from app.schemas import CompleteLifecycleRequest, InviteUserRequest
from app.schemas.account_lifecycle import ForgotPasswordRequest, UpdateEmailSettingsRequest
from app.services.auth.account_lifecycle import (
    activate_invitation,
    complete_password_reset,
    forgot_password,
    inspect_token,
    invite_user,
    issue_admin_reset,
)
from app.services.auth.email_delivery import render_message, send_smtp


class _Socket:
    def settimeout(self, value: int) -> None:
        self.timeout = value


class _Smtp:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.sock = _Socket()
        self.started_tls = False
        self.logged_in = False
        self.sent = False

    def __enter__(self) -> "_Smtp":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self, **_kwargs: object) -> None:
        self.started_tls = True

    def login(self, _username: str, _password: str) -> None:
        self.logged_in = True

    def send_message(self, _message: object) -> None:
        self.sent = True


class AccountLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.config = EmailConfigEntity(
            delivery_mode="manual-link",
            public_app_url="https://control.example.test",
            invite_expiry_minutes=60,
            reset_expiry_minutes=15,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _admin(self) -> UserEntity:
        return users.create(
            self.session,
            email="admin@example.test",
            name="Admin",
            role="admin",
            auth_provider="local",
            password_hash="hash",
            assigned_instances=[],
            is_active=True,
        )

    @patch("app.services.auth.account_lifecycle.ensure_local_auth_allowed")
    def test_InviteActivate_RawTokenNotStoredAndCannotBeReused(self, _allowed: object) -> None:
        self._admin()
        with patch(
            "app.services.auth.account_lifecycle.get_runtime_config",
            return_value=self.config,
        ):
            response = invite_user(
                InviteUserRequest(
                    email="invitee@example.test",
                    name="Invitee",
                    role="viewer",
                ),
                self.session,
                {"role": "admin", "email": "admin@example.test"},
            )
        self.assertIsNotNone(response.manual_link)
        raw = parse_qs(urlparse(response.manual_link or "").query)["token"][0]
        stored = self.session.exec(select(AccountLifecycleTokenEntity)).one()
        self.assertNotEqual(stored.token_hash, raw)
        self.assertNotIn(raw, stored.token_hash)
        self.assertTrue(inspect_token(self.session, raw, "invite").valid)

        activate_invitation(
            CompleteLifecycleRequest(token=raw, password="Validpass123!"),
            self.session,
        )
        self.assertFalse(inspect_token(self.session, raw, "invite").valid)
        with self.assertRaises(BadRequestError):
            activate_invitation(
                CompleteLifecycleRequest(token=raw, password="Anotherpass123!"),
                self.session,
            )

    def test_AdminReset_ReissueRevokesOldAndResetIncrementsCredentialVersion(self) -> None:
        admin = self._admin()
        user = users.create(
            self.session,
            email="user@example.test",
            name="User",
            role="viewer",
            auth_provider="local",
            password_hash="old",
            assigned_instances=[],
            is_active=True,
        )
        claims = {"role": "admin", "email": admin.email}
        with patch(
            "app.services.auth.account_lifecycle.get_runtime_config",
            return_value=self.config,
        ):
            first = issue_admin_reset(user.id or 0, self.session, claims)
            second = issue_admin_reset(user.id or 0, self.session, claims)
        first_token = parse_qs(urlparse(first.manual_link or "").query)["token"][0]
        second_token = parse_qs(urlparse(second.manual_link or "").query)["token"][0]
        self.assertFalse(inspect_token(self.session, first_token, "password_reset").valid)
        self.assertTrue(inspect_token(self.session, second_token, "password_reset").valid)

        complete_password_reset(
            CompleteLifecycleRequest(token=second_token, password="Updatedpass123!"),
            self.session,
        )
        refreshed = users.find_by_id(self.session, user.id or 0)
        self.assertEqual(refreshed.credential_version if refreshed else -1, 1)
        self.assertFalse(inspect_token(self.session, second_token, "password_reset").valid)

    def test_InvalidExpiredAndWrongPurposeTokensAreRejected(self) -> None:
        user = self._admin()
        entity = account_lifecycle.create_token(
            self.session,
            user_id=user.id,
            purpose="invite",
            token_hash="deadbeef",
            expires_at=datetime.utcnow() - timedelta(seconds=1),
        )
        self.assertFalse(inspect_token(self.session, "unknown-token-value-123", "invite").valid)
        self.assertFalse(inspect_token(self.session, "unknown-token-value-123", "password_reset").valid)
        entity.revoked_at = datetime.utcnow()
        account_lifecycle.save_token(self.session, entity)
        self.assertIsNotNone(entity.revoked_at)

    def test_SecurityEventsContainNoBearerToken(self) -> None:
        self._admin()
        with (
            patch(
                "app.services.auth.account_lifecycle.get_runtime_config",
                return_value=self.config,
            ),
            patch("app.services.auth.account_lifecycle.ensure_local_auth_allowed"),
        ):
            response = invite_user(
                InviteUserRequest(email="safe@example.test", name="Safe"),
                self.session,
                {"role": "admin", "email": "admin@example.test"},
            )
        raw = parse_qs(urlparse(response.manual_link or "").query)["token"][0]
        events = self.session.exec(select(SecurityEventEntity)).all()
        self.assertTrue(events)
        self.assertTrue(all(raw not in event.detail for event in events))

    def test_ForgotPassword_ResponseDoesNotEnumerateAccounts(self) -> None:
        active = users.create(
            self.session,
            email="active@example.test",
            name="Active",
            role="viewer",
            auth_provider="local",
            password_hash="hash",
            assigned_instances=[],
            is_active=True,
        )
        users.create(
            self.session,
            email="inactive@example.test",
            name="Inactive",
            role="viewer",
            auth_provider="local",
            password_hash=None,
            assigned_instances=[],
            is_active=False,
        )
        users.create(
            self.session,
            email="oidc@example.test",
            name="OIDC",
            role="viewer",
            auth_provider="oidc",
            password_hash=None,
            assigned_instances=[],
            is_active=True,
        )
        smtp_config = EmailConfigEntity(
            delivery_mode="smtp",
            smtp_host="mail.example.test",
            sender_email="noreply@example.test",
            public_app_url="https://control.example.test",
        )
        responses: list[str] = []
        with (
            patch(
                "app.services.auth.account_lifecycle.get_runtime_config",
                return_value=smtp_config,
            ),
            patch(
                "app.services.auth.account_lifecycle.send_smtp",
                side_effect=BadRequestError("SMTP delivery failed (SMTPAuthenticationError)"),
            ),
        ):
            for email in (
                active.email,
                "unknown@example.test",
                "inactive@example.test",
                "oidc@example.test",
            ):
                responses.append(
                    forgot_password(
                        ForgotPasswordRequest(email=email),
                        self.session,
                        client_key=f"client-{email}",
                    ).message
                )
        self.assertEqual(len(set(responses)), 1)
        event_text = " ".join(event.detail for event in self.session.exec(select(SecurityEventEntity)).all())
        self.assertNotIn("SMTPAuthenticationError", event_text)
        self.assertNotIn(active.email, event_text)


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = EmailConfigEntity(
            delivery_mode="smtp",
            smtp_host="mail.example.test",
            smtp_port=587,
            smtp_tls_mode="starttls",
            smtp_username="user",
            smtp_password_enc="password",
            sender_email="noreply@example.test",
            sender_name="Triton Control",
            public_app_url="https://control.example.test",
        )

    def test_RenderMessage_EscapesHtmlAndRejectsUnknownPlaceholders(self) -> None:
        message = render_message(
            self.config,
            purpose="invite",
            display_name="<Admin>",
            link="https://example.test/?a=1&b=2",
            expiry_minutes=60,
        )
        self.assertIn("&lt;Admin&gt;", message.html)
        self.config.invite_html_template = "<p>{danger}</p>{link}"
        with self.assertRaises(BadRequestError):
            render_message(
                self.config,
                purpose="invite",
                display_name="Admin",
                link="https://example.test/",
                expiry_minutes=60,
            )

    def test_UpdateSettings_Port465WithoutImplicitTls_IsRejectedImmediately(self) -> None:
        with self.assertRaisesRegex(ValueError, "port 465 requires implicit TLS"):
            UpdateEmailSettingsRequest(
                delivery_mode="smtp",
                smtp_host="smtp.example.test",
                smtp_port=465,
                smtp_tls_mode="starttls",
                sender_email="noreply@example.test",
                public_app_url="https://control.example.test",
            )

    def test_SendSmtp_StartTlsAuthAndSubmission(self) -> None:
        client = _Smtp()
        with patch(
            "app.services.auth.email_delivery.smtp_password",
            return_value="secret",
        ):
            send_smtp(
                self.config,
                "recipient@example.test",
                render_message(
                    self.config,
                    purpose="password_reset",
                    display_name="User",
                    link="https://example.test/reset",
                    expiry_minutes=15,
                ),
                smtp_factory=lambda *_args, **_kwargs: client,
            )
        self.assertTrue(client.started_tls)
        self.assertTrue(client.logged_in)
        self.assertTrue(client.sent)

    def test_SendSmtp_ImplicitTlsUsesSslFactory(self) -> None:
        self.config.smtp_tls_mode = "tls"
        client = _Smtp()
        calls: list[tuple[object, ...]] = []

        def ssl_factory(*args: object, **_kwargs: object) -> _Smtp:
            calls.append(args)
            return client

        with patch(
            "app.services.auth.email_delivery.smtp_password",
            return_value="secret",
        ):
            send_smtp(
                self.config,
                "recipient@example.test",
                render_message(
                    self.config,
                    purpose="password_reset",
                    display_name="User",
                    link="https://example.test/reset",
                    expiry_minutes=15,
                ),
                smtp_ssl_factory=ssl_factory,
            )
        self.assertEqual(calls[0][:2], ("mail.example.test", 587))
        self.assertFalse(client.started_tls)
        self.assertTrue(client.sent)

    def test_SendSmtp_SanitizesConnectionAuthenticationAndRecipientFailures(self) -> None:
        failures = (
            TimeoutError("smtp-password secret"),
            smtplib.SMTPAuthenticationError(535, b"credential rejected"),
            smtplib.SMTPRecipientsRefused({"recipient@example.test": (550, b"sensitive upstream response")}),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(BadRequestError) as raised:
                    send_smtp(
                        self.config,
                        "recipient@example.test",
                        render_message(
                            self.config,
                            purpose="password_reset",
                            display_name="User",
                            link="https://example.test/reset",
                            expiry_minutes=15,
                        ),
                        smtp_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
                    )
                message = str(raised.exception)
                self.assertIn(type(failure).__name__, message)
                self.assertNotIn("smtp-password", message)
                self.assertNotIn("upstream", message)

    def test_SendSmtp_ServerDisconnected_ExplainsEndpointConfiguration(self) -> None:
        with self.assertRaises(BadRequestError) as raised:
            send_smtp(
                self.config,
                "recipient@example.test",
                render_message(
                    self.config,
                    purpose="password_reset",
                    display_name="User",
                    link="https://example.test/reset",
                    expiry_minutes=15,
                ),
                smtp_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    smtplib.SMTPServerDisconnected("connection closed")
                ),
            )

        message = str(raised.exception)
        self.assertIn("SMTPServerDisconnected", message)
        self.assertIn("verify the SMTP host, port, and TLS mode", message)
        self.assertIn("not IMAP", message)
        self.assertNotIn("connection closed", message)

    def test_SendSmtp_PlainWithoutOptInIsRejected(self) -> None:
        self.config.smtp_tls_mode = "none"
        with self.assertRaises(BadRequestError):
            send_smtp(
                self.config,
                "recipient@example.test",
                render_message(
                    self.config,
                    purpose="password_reset",
                    display_name="User",
                    link="https://example.test/reset",
                    expiry_minutes=15,
                ),
            )

    def test_EmailSecretEncryption_RoundTripsAndRejectsWrongKey(self) -> None:
        with patch.dict(os.environ, {"EMAIL_SECRET_ENCRYPTION_KEY": "first-key"}):
            encrypted = encrypt_email_secret("smtp-password")
            self.assertNotIn("smtp-password", encrypted)
            self.assertEqual(decrypt_email_secret(encrypted), "smtp-password")
        with patch.dict(os.environ, {"EMAIL_SECRET_ENCRYPTION_KEY": "other-key"}):
            with self.assertRaises(ValueError):
                decrypt_email_secret(encrypted)
