import unittest
from datetime import datetime, timedelta, timezone

from app.radar_expiry import RADAR_SIGNAL_TTL, expiry_at, is_expired


class RadarSignalExpiryTests(unittest.TestCase):
    def test_ttl_is_exactly_24_hours(self):
        self.assertEqual(RADAR_SIGNAL_TTL, timedelta(hours=24))

    def test_expiry_is_based_on_original_creation_time(self):
        created = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(expiry_at(created), created + timedelta(hours=24))

    def test_signal_is_active_just_before_expiry(self):
        expires = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(is_expired(expires, expires - timedelta(microseconds=1)))

    def test_signal_expires_at_boundary(self):
        expires = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(is_expired(expires, expires))

    def test_naive_sqlite_datetime_is_treated_as_utc(self):
        expires = datetime(2026, 10, 2, 12, 0)
        now = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
        self.assertTrue(is_expired(expires, now))

    def test_missing_expiry_is_not_assumed_expired(self):
        self.assertFalse(is_expired(None))


if __name__ == "__main__":
    unittest.main()
