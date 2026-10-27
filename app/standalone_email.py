"""Email delivery for standalone SAS accounts.

This module is deliberately not wired to any public endpoint until SMTP is configured
and verified. It fails closed when delivery is not configured and never logs credentials
or message contents.
"""
import asyncio
import smtplib
from email.message import EmailMessage

from .config import settings


class EmailDeliveryNotConfigured(RuntimeError):
    """Raised when outbound email cannot safely be attempted."""


def _smtp_config() -> dict[str, object]:
    host = settings.standalone_smtp_host.strip()
    sender = settings.standalone_email_from.strip()
    if not host or not sender:
        raise EmailDeliveryNotConfigured("Standalone email delivery is not configured.")
    port = settings.standalone_smtp_port
    if not 1 <= port <= 65535:
        raise EmailDeliveryNotConfigured("Standalone SMTP port is invalid.")
    if settings.standalone_smtp_use_tls and settings.standalone_smtp_use_ssl:
        raise EmailDeliveryNotConfigured("Configure either SMTP STARTTLS or implicit SSL, not both.")
    if settings.standalone_smtp_username and not settings.standalone_smtp_password:
        raise EmailDeliveryNotConfigured("SMTP password is missing for the configured username.")
    return {
        "host": host,
        "port": port,
        "sender": sender,
        "username": settings.standalone_smtp_username.strip(),
        "password": settings.standalone_smtp_password,
        "starttls": settings.standalone_smtp_use_tls,
        "ssl": settings.standalone_smtp_use_ssl,
    }


def _send_sync(recipient: str, subject: str, text_body: str) -> None:
    config = _smtp_config()
    message = EmailMessage()
    message["From"] = str(config["sender"])
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text_body)
    client_type = smtplib.SMTP_SSL if config["ssl"] else smtplib.SMTP
    with client_type(str(config["host"]), int(config["port"]), timeout=15) as client:
        client.ehlo()
        if config["starttls"]:
            client.starttls()
            client.ehlo()
        if config["username"]:
            client.login(str(config["username"]), str(config["password"]))
        client.send_message(message)


async def send_standalone_email(recipient: str, subject: str, text_body: str) -> None:
    """Send plain-text account email or fail without pretending it was delivered."""
    config = _smtp_config()
    if not recipient or "\n" in recipient or "\r" in recipient:
        raise ValueError("A valid recipient address is required.")
    if not subject or "\n" in subject or "\r" in subject:
        raise ValueError("A valid email subject is required.")
    if not text_body.strip():
        raise ValueError("Email body cannot be empty.")
    # Validate config before handing off to the worker thread.
    del config
    await asyncio.to_thread(_send_sync, recipient, subject, text_body)
