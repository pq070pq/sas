import unittest

from app.db import Base, SASEmailOutbox, SASAccount, SASAuthToken, SASIdentityLink, SASLoginSession, User, Subscription, Payment
from app.standalone_auth import digest_token, hash_password, new_bearer_token, normalize_email, validate_password, verify_password


class StandaloneIdentitySchemaTests(unittest.TestCase):
    def test_new_tables_are_additive(self):
        for name in ("sas_accounts", "sas_identity_links", "sas_auth_tokens", "sas_login_sessions", "sas_email_outbox",
                     "users", "subscriptions", "payments"):
            self.assertIn(name, Base.metadata.tables)

    def test_outbox_schema_has_no_plaintext_token_fields(self):
        columns = SASEmailOutbox.__table__.columns
        self.assertIn("body", columns)
        self.assertIn("dedupe_key", columns)
        self.assertNotIn("token", columns)
        self.assertNotIn("raw_token", columns)
        self.assertTrue(columns["dedupe_key"].unique)

    def test_telegram_schema_remains_unchanged(self):
        self.assertIn("telegram_id", User.__table__.columns)
        self.assertIn("telegram_id", Subscription.__table__.columns)
        self.assertIn("telegram_id", Payment.__table__.columns)
        self.assertIn("telegram_charge_id", Payment.__table__.columns)
        self.assertNotIn("email", User.__table__.columns)

    def test_identity_links_and_email_are_unique(self):
        self.assertTrue(SASAccount.__table__.columns["email"].unique)
        constraints = {
            tuple(column.name for column in item.columns)
            for item in SASIdentityLink.__table__.constraints
            if item.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("provider", "provider_subject"), constraints)

    def test_token_records_store_digests_not_raw_tokens(self):
        for model in (SASAuthToken, SASLoginSession):
            self.assertIn("token_digest", model.__table__.columns)
            self.assertNotIn("raw_token", model.__table__.columns)


class StandaloneAuthPrimitiveTests(unittest.TestCase):
    def test_email_normalization(self):
        self.assertEqual(normalize_email(" User@Example.COM "), "user@example.com")
        with self.assertRaises(ValueError):
            normalize_email("not-an-email")

    def test_password_hash_and_verify(self):
        encoded = hash_password("a-strong-test-password", iterations=100_000)
        self.assertTrue(verify_password("a-strong-test-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        self.assertFalse(verify_password("a-strong-test-password", "invalid"))
        with self.assertRaises(ValueError):
            validate_password("short")

    def test_tokens_are_random_and_digest_is_one_way_representation(self):
        first, second = new_bearer_token(), new_bearer_token()
        self.assertNotEqual(first, second)
        self.assertNotEqual(digest_token(first), first)
        self.assertEqual(len(digest_token(first)), 64)
        with self.assertRaises(ValueError):
            digest_token("short")


if __name__ == "__main__":
    unittest.main()
