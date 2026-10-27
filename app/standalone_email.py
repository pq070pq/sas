"""Email delivery for standalone SAS accounts.

Not wired to public endpoints yet. SMTP settings use the STANDALONE_ environment
prefix and the existing .env file. Delivery fails closed when configuration is absent.
"""
import asyncio
import smtplib
from email.message import EmailMessage

from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailDeliveryNotConfigured(RuntimeError):
    """Raised when outbound email cannot safely be attempted."""


class StandaloneEmailSettings(BaseSettings):
    smtp_host: str = ""
    smtp_port: int = 587
    email_from: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STANDALONE_", extra="ignore")


email_settings = StandaloneEmailSettings()


def _smtp_config() -> dict[str, object]:
    host = email_settings.smtp_host.strip()
    sender = email_settings.email_from.strip()
    if not host or not sender:
        raise EmailDeliveryNotConfigured("Standalone email delivery is not configured.")
    port = email_settings.smtp_port
    if not 1 <= port <= 65535:
        raise EmailDeliveryNotConfigured("Standalone SMTP port is invalid.")
    if email_settings.smtp_use_tls and email_settings.smtp_use_ssl:
        raise EmailDeliveryNotConfigured("Configure either SMTP STARTTLS or implicit SSL, not both.")
    if email_settings.smtp_username and not email_settings.smtp_password:
        raise EmailDeliveryNotConfigured("SMTP password is missing for the configured username.")
    return {
        "host": host, "port": port, "sender": sender,
        "username": email_settings.smtp_username.strip(),
        "password": email_settings.smtp_password,
        "starttls": email_settings.smtp_use_tls, "ssl": email_settings.smtp_use_ssl,
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
    """Send plain-text account email; never report success before SMTP accepts it."""
    _smtp_config()
    if not recipient or "\n" in recipient or "\r" in recipient:
        raise ValueError("A valid recipient address is required.")
    if not subject or "\n" in subject or "\r" in subject:
        raise ValueError("A valid email subject is required.")
    if not text_body.strip():
        raise ValueError("Email body cannot be empty.")
    await asyncio.to_thread(_send_sync, recipient, subject, text_body)
