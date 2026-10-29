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



class _YahooResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class YahooOhlcvFallbackTests(unittest.IsolatedAsyncioTestCase):
    def _payload(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        timestamps = [int((now - timedelta(days=39 - i)).timestamp()) for i in range(40)]
        return {
            "chart": {
                "result": [{
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [{
                            "open": [1.0] * 40,
                            "high": [1.2] * 40,
                            "low": [0.9] * 40,
                            "close": [1.1] * 40,
                            "volume": [100000] * 40,
                        }]
                    },
                }],
                "error": None,
            }
        }

    async def test_yahoo_fallback_is_used_only_when_explicitly_allowed(self):
        payload = self._payload()

        class FakeClient:
            urls = []

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, *args, **kwargs):
                self.urls.append(url)
                if "query1.finance.yahoo.com/v8/finance/chart/" in url:
                    return _YahooResponse(payload)
                raise AssertionError(f"unexpected network source: {url}")

        with patch("app.panwatch.httpx.AsyncClient", FakeClient), patch(
            "app.panwatch._ohlcv_provider_available", return_value=False
        ):
            candles, source, diagnostics = await panwatch.ohlcv(
                "TEST",
                days=90,
                min_candles=30,
                allow_twelve_data=False,
                timeout_seconds=8,
                allow_yahoo=True,
                skip_panwatch=True,
            )

        self.assertEqual(len(candles), 40)
        self.assertEqual(source, "Yahoo Finance Candles")
        self.assertTrue(any("query1.finance.yahoo.com" in url for url in FakeClient.urls))

        FakeClient.urls.clear()
        with patch("app.panwatch.httpx.AsyncClient", FakeClient), patch(
            "app.panwatch._ohlcv_provider_available", return_value=False
        ):
            candles, source, diagnostics = await panwatch.ohlcv(
                "TEST",
                days=90,
                min_candles=30,
                allow_twelve_data=False,
                timeout_seconds=8,
                allow_yahoo=False,
                skip_panwatch=True,
            )

        self.assertEqual(candles, [])
        self.assertEqual(source, "unavailable")
        self.assertFalse(any("query1.finance.yahoo.com" in url for url in FakeClient.urls))


if __name__ == "__main__":
    unittest.main()
