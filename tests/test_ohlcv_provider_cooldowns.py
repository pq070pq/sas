import unittest
from unittest.mock import patch

from app import panwatch


class OhlcvProviderCooldownTests(unittest.TestCase):
    def setUp(self):
        panwatch._OHLCV_PROVIDER_COOLDOWNS.clear()
        panwatch._OHLCV_PROVIDER_FAILURE_STREAKS.clear()

    def tearDown(self):
        panwatch._OHLCV_PROVIDER_COOLDOWNS.clear()
        panwatch._OHLCV_PROVIDER_FAILURE_STREAKS.clear()

    def test_provider_cools_down_after_repeated_failures(self):
        with patch("app.panwatch.time.monotonic", return_value=100.0):
            panwatch._ohlcv_provider_failed("finnhub", threshold=3, cooldown_seconds=900)
            panwatch._ohlcv_provider_failed("finnhub", threshold=3, cooldown_seconds=900)
            self.assertTrue(panwatch._ohlcv_provider_available("finnhub"))
            panwatch._ohlcv_provider_failed("finnhub", threshold=3, cooldown_seconds=900)
            self.assertFalse(panwatch._ohlcv_provider_available("finnhub"))

    def test_expired_cooldown_allows_provider_to_recover(self):
        with patch("app.panwatch.time.monotonic", return_value=100.0):
            panwatch._ohlcv_provider_failed("stooq", threshold=1, cooldown_seconds=300, immediate=True)
            self.assertFalse(panwatch._ohlcv_provider_available("stooq"))
        with patch("app.panwatch.time.monotonic", return_value=401.0):
            self.assertTrue(panwatch._ohlcv_provider_available("stooq"))

    def test_success_clears_failure_streak_and_cooldown(self):
        with patch("app.panwatch.time.monotonic", return_value=100.0):
            panwatch._ohlcv_provider_failed("fmp", threshold=1, cooldown_seconds=300, immediate=True)
            panwatch._ohlcv_provider_succeeded("fmp")
            self.assertTrue(panwatch._ohlcv_provider_available("fmp"))
            self.assertNotIn("fmp", panwatch._OHLCV_PROVIDER_FAILURE_STREAKS)


if __name__ == "__main__":
    unittest.main()
