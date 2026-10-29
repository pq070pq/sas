import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

from app.market import _panwatch_extended_quote


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        return _Response({
            "data": [
                {"datetime": "2026-10-29T08:00:00-04:00", "open": 1.00, "close": 1.02, "volume": 300000},
                {"datetime": "2026-10-29T08:04:00-04:00", "open": 1.02, "close": 1.10, "volume": 250000},
            ]
        })


class ExtendedQuoteFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_premarket_bars_produce_explicit_session_quote(self):
        now = datetime(2026, 10, 29, 8, 5, tzinfo=ZoneInfo("America/New_York"))
        with patch("app.market.httpx.AsyncClient", _FakeClient):
            result = await _panwatch_extended_quote("POM", "premarket", now=now)

        self.assertEqual(result["price"], 1.10)
        self.assertAlmostEqual(result["change_pct"], 10.0, places=5)
        self.assertEqual(result["session_volume"], 550000)
        self.assertTrue(result["is_extended_hours"])
        self.assertFalse(result["is_overnight_hours"])
        self.assertEqual(result["source"], "PanWatch Extended 1m")

    async def test_stale_extended_bars_are_not_promoted_to_live_quote(self):
        class StaleClient(_FakeClient):
            async def get(self, url, params=None):
                return _Response({
                    "data": [
                        {"datetime": "2026-10-29T07:00:00-04:00", "open": 1.00, "close": 1.10, "volume": 550000},
                    ]
                })

        now = datetime(2026, 10, 29, 8, 5, tzinfo=ZoneInfo("America/New_York"))
        with patch("app.market.httpx.AsyncClient", StaleClient):
            result = await _panwatch_extended_quote("POM", "premarket", now=now)

        self.assertIsNone(result)

    async def test_regular_session_is_not_treated_as_extended(self):
        result = await _panwatch_extended_quote(
            "POM",
            "regular",
            now=datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
