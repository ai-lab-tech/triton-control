"""SMTP delivery and constrained lifecycle-message rendering."""

from __future__ import annotations

import html
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from string import Formatter
from typing import Callable

from app.db.entities import EmailConfigEntity
from app.exceptions import BadRequestError
from app.services.auth.email_settings import smtp_password

DEFAULT_INVITE_TEXT = (
    "Hello {display_name},\n\nYou are invited to {product_name}. "
    "Set your password within {expiry_minutes} minutes:\n{link}\n\n"
    "If you did not expect this invitation, ignore this message."
)
DEFAULT_INVITE_HTML = (
    "<p>Hello {display_name},</p><p>You are invited to {product_name}.</p>"
    '<p><a href="{link}">Set your password</a> within {expiry_minutes} minutes.</p>'
    "<p>If you did not expect this invitation, ignore this message.</p>"
)
DEFAULT_RESET_TEXT = (
    "Hello {display_name},\n\nReset your {product_name} password within "
    "{expiry_minutes} minutes:\n{link}\n\n"
    "If you did not request this reset, ignore this message."
)
DEFAULT_RESET_HTML = (
    '<p>Hello {display_name},</p><p><a href="{link}">Reset your '
    "{product_name} password</a> within {expiry_minutes} minutes.</p>"
    "<p>If you did not request this reset, ignore this message.</p>"
)
ALLOWED_FIELDS = {"display_name", "product_name", "link", "expiry_minutes"}


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    text: str
    html: str


def _validate_template(template: str) -> None:
    fields = {name for _, name, _, _ in Formatter().parse(template) if name}
    if not fields.issubset(ALLOWED_FIELDS):
        raise BadRequestError("Email template contains unsupported placeholders")
    if "link" not in fields:
        raise BadRequestError("Email template must contain {link}")


def render_message(
    config: EmailConfigEntity,
    *,
    purpose: str,
    display_name: str,
    link: str,
    expiry_minutes: int,
) -> RenderedMessage:
    invite = purpose == "invite"
    text_template = (
        config.invite_text_template or DEFAULT_INVITE_TEXT
        if invite
        else config.reset_text_template or DEFAULT_RESET_TEXT
    )
    html_template = (
        config.invite_html_template or DEFAULT_INVITE_HTML
        if invite
        else config.reset_html_template or DEFAULT_RESET_HTML
    )
    _validate_template(text_template)
    _validate_template(html_template)
    text_values = {
        "display_name": display_name,
        "product_name": "Triton Control",
        "link": link,
        "expiry_minutes": str(expiry_minutes),
    }
    html_values = {key: html.escape(value, quote=True) for key, value in text_values.items()}
    return RenderedMessage(
        subject=config.invite_subject if invite else config.reset_subject,
        text=text_template.format_map(text_values),
        html=html_template.format_map(html_values),
    )


def _ssl_context(config: EmailConfigEntity) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if config.ca_certificate.strip():
        context.load_verify_locations(cadata=config.ca_certificate)
    return context


def _smtp_failure_message(exc: BaseException) -> str:
    error_type = type(exc).__name__
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        guidance = "authentication failed; verify the SMTP username and password"
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        guidance = (
            "the server disconnected; verify the SMTP host, port, and TLS mode "
            "and ensure this is an SMTP endpoint, not IMAP"
        )
    elif isinstance(exc, ssl.SSLError):
        guidance = "TLS negotiation failed; verify the TLS mode and certificate trust"
    elif isinstance(exc, TimeoutError):
        guidance = "the connection timed out; verify the SMTP host, port, and network access"
    else:
        guidance = "verify the SMTP host, port, TLS mode, credentials, and recipient"
    return f"SMTP delivery failed ({error_type}): {guidance}"


def send_smtp(
    config: EmailConfigEntity,
    recipient: str,
    message: RenderedMessage,
    *,
    smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    smtp_ssl_factory: Callable[..., smtplib.SMTP_SSL] | None = None,
) -> None:
    if config.delivery_mode != "smtp":
        raise BadRequestError("SMTP delivery is not enabled")
    if config.smtp_tls_mode == "none" and not config.smtp_allow_insecure:
        raise BadRequestError("Plain SMTP requires explicit insecure transport opt-in")
    mail = EmailMessage()
    mail["Subject"] = message.subject
    mail["From"] = f"{config.sender_name} <{config.sender_email}>"
    mail["To"] = recipient
    mail.set_content(message.text)
    mail.add_alternative(message.html, subtype="html")
    context = _ssl_context(config)
    smtp_factory = smtp_factory or smtplib.SMTP
    smtp_ssl_factory = smtp_ssl_factory or smtplib.SMTP_SSL
    try:
        client: smtplib.SMTP
        if config.smtp_tls_mode == "tls":
            client = smtp_ssl_factory(
                config.smtp_host,
                config.smtp_port,
                timeout=config.connect_timeout_seconds,
                context=context,
            )
        else:
            client = smtp_factory(config.smtp_host, config.smtp_port, timeout=config.connect_timeout_seconds)
        with client:
            sock = getattr(client, "sock", None)
            if sock is not None:
                sock.settimeout(config.operation_timeout_seconds)
            if config.smtp_tls_mode == "starttls":
                client.starttls(context=context)
            if config.smtp_username:
                client.login(config.smtp_username, smtp_password(config))
            client.send_message(mail)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        raise BadRequestError(_smtp_failure_message(exc)) from exc
