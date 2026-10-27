import unittest
from unittest.mock import patch

from app.standalone_email import (
    EmailDeliveryNotConfigured,
    email_settings,
    send_standalone_email,
)


class StandaloneEmailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = {
            name: getattr(email_settings, name)
            for name in (
                "smtp_host", "smtp_port", "email_from", "smtp_username",
                "smtp_password", "smtp_use_tls", "smtp_use_ssl",
            )
        }

    def tearDown(self):
        for name, value in self.original.items():
            setattr(email_settings, name, value)

    async def test_missing_delivery_config_fails_closed(self):
        email_settings.smtp_host = ""
        email_settings.email_from = ""
        with self.assertRaises(EmailDeliveryNotConfigured):
            await send_standalone_email("user@example.com", "Verify account", "Code: 123")

    async def test_tls_modes_cannot_both_be_enabled(self):
        email_settings.smtp_host = "smtp.example.com"
        email_settings.email_from = "no-reply@example.com"
        email_settings.smtp_use_tls = True
        email_settings.smtp_use_ssl = True
        with self.assertRaises(EmailDeliveryNotConfigured):
            await send_standalone_email("user@example.com", "Verify account", "Code: 123")

    async def test_rejects_header_injection_before_network_access(self):
        email_settings.smtp_host = "smtp.example.com"
        email_settings.email_from = "no-reply@example.com"
        email_settings.smtp_use_tls = True
        email_settings.smtp_use_ssl = False
        with patch("app.standalone_email.asyncio.to_thread") as worker:
            with self.assertRaises(ValueError):
                await send_standalone_email("user@example.com\nBcc: victim@example.com", "Verify", "Code")
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
