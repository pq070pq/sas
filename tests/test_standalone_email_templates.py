import unittest
from urllib.parse import parse_qs, urlsplit

from app.standalone_email_templates import (
    InvalidStandalonePublicURL,
    password_reset_email,
    validate_public_origin,
    verification_email,
)


class StandaloneEmailTemplateTests(unittest.TestCase):
    def test_valid_https_origin_is_normalized(self):
        self.assertEqual(validate_public_origin(" https://sas.example.com/ "), "https://sas.example.com")

    def test_rejects_insecure_or_non_origin_urls(self):
        for origin in (
            "http://sas.example.com",
            "https://sas.example.com/path",
            "https://user:pass@sas.example.com",
            "https://sas.example.com?next=evil",
            "not a url",
        ):
            with self.subTest(origin=origin), self.assertRaises(InvalidStandalonePublicURL):
                validate_public_origin(origin)

    def test_verification_link_encodes_token_and_uses_fixed_path(self):
        token = "token-with-special_Chars-1234567890"
        subject, body = verification_email("https://sas.example.com", token)
        self.assertEqual(subject, "Verify your SAS PRO email")
        url = next(line for line in body.splitlines() if line.startswith("https://"))
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "sas.example.com")
        self.assertEqual(parsed.path, "/standalone/verify-email")
        self.assertEqual(parse_qs(parsed.query)["token"], [token])

    def test_reset_link_uses_fixed_path_and_expiry_notice(self):
        subject, body = password_reset_email("https://sas.example.com", "r" * 32)
        self.assertEqual(subject, "Reset your SAS PRO password")
        self.assertIn("/standalone/reset-password?", body)
        self.assertIn("20 minutes", body)

    def test_unsupported_path_and_malformed_token_are_rejected(self):
        from app.standalone_email_templates import _link
        with self.assertRaises(ValueError):
            _link("https://sas.example.com", "/redirect", "t" * 32)
        with self.assertRaises(ValueError):
            verification_email("https://sas.example.com", "bad token")


if __name__ == "__main__":
    unittest.main()
