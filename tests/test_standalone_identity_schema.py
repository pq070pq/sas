import unittest

from app.db import Base, SASAccount, SASAuthToken, SASIdentityLink, SASLoginSession, User, Subscription, Payment
from app.standalone_auth import (
    digest_token,
    hash_password,
    new_bearer_token,
    normalize_email,
    validate_password,
    verify_password,
)


class StandaloneIdentitySchemaTests(unittest.TestCase):
    def test_standalone_tables_are_additive(self):
        for table in (
            "sas_accounts", "sas_identity_links", "sas_auth_tokens",
            "sas_login_sessions", "users", "subscriptions", "payments",
        ):
            self.assertIn(table, Base.metadata.tables)

    def test_legacy_telegram_models_keep_existing_keys(self):
        self.assertIn("telegram_id", User.__table__.columns)
        self.assertIn("telegram_id", Subscription.__table__.columns)
        self.assertIn("telegram_id", Payment.__table__.columns)
        self.assertIn("telegram_charge_id", Payment.__table__.columns)
        self.assertNotIn("email", User.__table__.columns)

    def test_account_email_unique_and_password_optional_until_signup_flow(self):
        account = SASAccount.__table__
        self.assertTrue(account.columns["email"].unique)
        self.assertTrue(account.columns["password_hash"].nullable)

    def test_identity_link_is_unique_per_provider_subject(self):
        table = SASIdentityLink.__table__
        constraints = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("provider", "provider_subject"), constraints)
        self.assertEqual(
            [fk.target_fullname for fk in table.columns["account_id"].foreign_keys],
            ["sas_accounts.id"],
        )

    def test_sessions_and_tokens_only_store_digest(self):
        self.assertIn("token_digest", SASAuthToken.__table__.columns)
        self.assertIn("token_digest", SASLoginSession.__table__.columns)
        self.assertNotIn("raw_token", SASAuthToken.__table__.columns)
        self.assertNotIn("raw_token", SASLoginSession.__table__.columns)


class StandaloneAuthPrimitiveTests(unittest.TestCase):
    def test_email_normalization_and_validation(self):
        self.assertEqual(normalize_email("  User@Example.COM "), "user@example.com")
        with self.assertRaises(ValueError):
            normalize_email("not-an-email")

    def test_password_hash_verification_and_policy(self):
        encoded = hash_password("a-strong-test-password", iterations=100_000)
        self.assertTrue(verify_password("a-strong-test-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        self.assertFalse(verify_password("a-strong-test-password", "malformed"))
        with self.assertRaises(ValueError):
            validate_password("short")

    def test_bearer_tokens_are_random_and_only_digest_is_persisted(self):
        token_a = new_bearer_token()
        token_b = new_bearer_token()
        self.assertNotEqual(token_a, token_b)
        self.assertNotEqual(digest_token(token_a), token_a)
        self.assertEqual(len(digest_token(token_a)), 64)
        with self.assertRaises(ValueError):
            digest_token("short")


if __name__ == "__main__":
    unittest.main()
