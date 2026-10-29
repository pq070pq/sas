import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from app import scanner


class RadarPriceRangeTests(unittest.TestCase):
    def test_configured_range_matches_user_requirement(self):
        self.assertEqual(scanner.MIN_PRICE, 0.30)
        self.assertEqual(scanner.MAX_PRICE, 6.00)

    def test_lower_and_upper_boundaries_are_included(self):
        for price in (0.30, 6.00):
            with self.subTest(price=price):
                row = {"price": price, "change_pct": 10.1, "volume": 500_001, "market_cap": 0}
                self.assertEqual(scanner._classify_momentum_candidate(row), "small")

    def test_prices_outside_range_do_not_pass_small_stock_gate(self):
        for price in (0.29, 6.01):
            with self.subTest(price=price):
                row = {"price": price, "change_pct": 10.1, "volume": 500_001, "market_cap": 0}
                self.assertIsNone(scanner._classify_momentum_candidate(row))

    def test_gap_watch_accepts_stock_that_gapped_from_in_range_previous_close(self):
        row = {
            "price": 8.34,
            "previous_close": 5.97,
            "change_pct": 39.7,
            "volume": 17_100_000,
            "quote_type": "EQUITY",
            "exchange": "NASDAQ",
        }
        self.assertTrue(scanner._is_gap_watch_candidate(row))

    def test_gap_watch_rejects_stock_without_in_range_previous_close(self):
        row = {
            "price": 8.34,
            "previous_close": 7.20,
            "change_pct": 39.7,
            "volume": 17_100_000,
            "quote_type": "EQUITY",
            "exchange": "NASDAQ",
        }
        self.assertFalse(scanner._is_gap_watch_candidate(row))

    def test_gap_watch_rejects_excessive_price_and_low_volume(self):
        base = {
            "price": 8.34,
            "previous_close": 5.97,
            "change_pct": 39.7,
            "volume": 17_100_000,
            "quote_type": "EQUITY",
            "exchange": "NASDAQ",
        }
        self.assertFalse(scanner._is_gap_watch_candidate({**base, "price": 12.01}))
        self.assertFalse(scanner._is_gap_watch_candidate({**base, "volume": 10_000}))

    def test_daily_momentum_filter_drops_out_of_range_rows_before_ohlcv(self):
        candidates = [
            {"symbol": "ABOVE", "price": 6.01, "change_pct": 30, "volume": 5_000_000},
            {"symbol": "BELOW", "price": 0.29, "change_pct": 30, "volume": 5_000_000},
        ]
        with patch(
            "app.scanner._momentum_time_filter",
            return_value=({"small": 1.0, "large": 1.0}, datetime(2026, 10, 29)),
        ):
            result, diagnostics = asyncio.run(scanner._apply_daily_momentum_filter(candidates))

        self.assertEqual(result, [])
        self.assertEqual(diagnostics["status"], "no_candidates_in_price_range")


if __name__ == "__main__":
    unittest.main()
