import os
import unittest
from unittest.mock import patch

from app import tradingview_mcp


class TradingViewMCPAdapterTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tradingview_mcp._endpoint_and_token(), (None, None))

    def test_rejects_non_loopback_endpoint(self):
        env = {
            "TV_MCP_RADAR_ENABLED": "1",
            "TV_MCP_URL": "http://example.com:3811/mcp",
            "TV_HTTP_TOKEN": "x" * 40,
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                tradingview_mcp._endpoint_and_token()

    def test_requires_long_bearer_token(self):
        env = {
            "TV_MCP_RADAR_ENABLED": "1",
            "TV_MCP_URL": "http://127.0.0.1:3811/mcp",
            "TV_HTTP_TOKEN": "too-short",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                tradingview_mcp._endpoint_and_token()

    def test_normalizes_raw_tradingview_rows(self):
        rows = tradingview_mcp._rows_from_data({
            "data": [{
                "s": "NASDAQ:ABCD",
                "d": ["ABCD", 1.25, 12.5, 900000, 50000000, "stock", "common", "Example Co"],
            }]
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "ABCD")
        self.assertEqual(rows[0]["exchange"], "NASDAQ")
        self.assertEqual(rows[0]["price"], 1.25)
        self.assertEqual(rows[0]["change_pct"], 12.5)
        self.assertEqual(rows[0]["source"], "TradingView MCP")

    def test_drops_rows_without_numeric_price(self):
        rows = tradingview_mcp._rows_from_data({"rows": [{"symbol": "NASDAQ:BAD", "price": "N/A"}]})
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
