"""Tests for the additive standalone installation-access schema and policy."""
import unittest
from datetime import datetime, timedelta, timezone

from app.db import Base, StandaloneInstallation, StandaloneAdminSession, StandaloneAdminAudit
from app.standalone_access import token_digest, installation_state, INITIAL_ACCESS_DAYS


class StandaloneAccessTests(unittest.TestCase):
    def make_row(self, **overrides):
        now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        values = dict(
            id="test-installation",
            token_hash=token_digest("random-install-token"),
            status="active",
            access_starts_at=now,
            access_expires_at=now + timedelta(days=30),
            permanent=False,
        )
        values.update(overrides)
        return StandaloneInstallation(**values), now

    def test_tables_are_additive(self):
        for table in ("standalone_installations", "standalone_admin_sessions", "standalone_admin_audit",
                      "users", "subscriptions", "payments"):
            self.assertIn(table, Base.metadata.tables)

    def test_initial_duration_is_thirty_days(self):
        self.assertEqual(INITIAL_ACCESS_DAYS, 30)

    def test_token_digest_is_one_way_deterministic_digest(self):
        self.assertEqual(token_digest("abc"), token_digest("abc"))
        self.assertNotEqual(token_digest("abc"), "abc")
        self.assertNotEqual(token_digest("abc"), token_digest("def"))

    def test_expiry_and_block_states(self):
        row, now = self.make_row()
        self.assertEqual(installation_state(row, now), "active")
        row.access_expires_at = now
        self.assertEqual(installation_state(row, now), "expired")
        row.status = "blocked"
        self.assertEqual(installation_state(row, now), "blocked")
        row.status = "revoked"
        self.assertEqual(installation_state(row, now), "revoked")

    def test_permanent_access_overrides_expiry_but_not_block(self):
        row, now = self.make_row(permanent=True, access_expires_at=now_minus_one())
        self.assertEqual(installation_state(row), "active")
        row.status = "blocked"
        self.assertEqual(installation_state(row), "blocked")

    def test_admin_models_have_separate_tables(self):
        self.assertIn("token_hash", StandaloneAdminSession.__table__.columns)
        self.assertIn("actor_username", StandaloneAdminAudit.__table__.columns)
        self.assertIn("token_hash", StandaloneInstallation.__table__.columns)


def now_minus_one():
    return datetime.now(timezone.utc) - timedelta(days=1)


if __name__ == "__main__":
    unittest.main()
