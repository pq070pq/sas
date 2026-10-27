"""Regression tests for the additive standalone-account schema."""

import unittest

from app.db import Base, SASAccount, SASIdentityLink, User, Subscription, Payment


class StandaloneIdentitySchemaTests(unittest.TestCase):
    def test_standalone_tables_are_additive(self):
        self.assertIn("sas_accounts", Base.metadata.tables)
        self.assertIn("sas_identity_links", Base.metadata.tables)
        self.assertIn("users", Base.metadata.tables)
        self.assertIn("subscriptions", Base.metadata.tables)
        self.assertIn("payments", Base.metadata.tables)

    def test_legacy_telegram_models_keep_their_existing_keys(self):
        self.assertIn("telegram_id", User.__table__.columns)
        self.assertIn("telegram_id", Subscription.__table__.columns)
        self.assertIn("telegram_id", Payment.__table__.columns)
        self.assertIn("telegram_charge_id", Payment.__table__.columns)
        self.assertNotIn("email", User.__table__.columns)

    def test_standalone_account_has_unique_email_and_optional_password_hash(self):
        account = SASAccount.__table__
        self.assertTrue(account.columns["email"].unique)
        self.assertTrue(account.columns["password_hash"].nullable)
        self.assertIn("email_verified_at", account.columns)
        self.assertIn("is_active", account.columns)

    def test_identity_link_has_provider_subject_uniqueness(self):
        table = SASIdentityLink.__table__
        unique_constraints = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("provider", "provider_subject"), unique_constraints)
        self.assertEqual(
            [fk.target_fullname for fk in table.columns["account_id"].foreign_keys],
            ["sas_accounts.id"],
        )


if __name__ == "__main__":
    unittest.main()
