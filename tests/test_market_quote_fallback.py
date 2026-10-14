import unittest
from unittest.mock import patch

from app.market import _stooq_quote


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, headers=None):
        if "/q/l/" in url:
            return _Response(
                "Symbol,Date,Time,Open,High,Low,Close,Volume,Name\n"
                "nok.us,2026-10-14,10:15:00,9.60,9.90,9.55,9.85,1000000,Nokia\n"
            )
        if "/q/d/l/" in url:
            return _Response(
                "Date,Open,High,Low,Close,Volume\n"
                "2026-10-13,9.30,9.60,9.20,9.50,2000000\n"
                "2026-10-14,9.60,9.90,9.55,9.80,1000000\n"
            )
        return _Response("", 404)


class StooqQuoteFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_quote_calculates_change_from_prior_close(self):
        with patch("app.market.httpx.AsyncClient", _FakeClient):
            result = await _stooq_quote("NOK")

        self.assertEqual(result["price"], 9.85)
        self.assertAlmostEqual(result["change_pct"], ((9.85 - 9.50) / 9.50) * 100, places=6)
        self.assertEqual(result["previous_close"], 9.50)

    async def test_snapshot_without_history_does_not_invent_change(self):
        class SnapshotOnlyClient(_FakeClient):
            async def get(self, url, params=None, headers=None):
                if "/q/l/" in url:
                    return _Response(
                        "Symbol,Date,Time,Open,High,Low,Close,Volume,Name\n"
                        "nok.us,2026-10-14,10:15:00,9.60,9.90,9.55,9.85,1000000,Nokia\n"
                    )
                return _Response("Date,Open,High,Low,Close,Volume\n")

        with patch("app.market.httpx.AsyncClient", SnapshotOnlyClient):
            result = await _stooq_quote("NOK")

        self.assertEqual(result["price"], 9.85)
        self.assertIsNone(result["change_pct"])


if __name__ == "__main__":
    unittest.main()
