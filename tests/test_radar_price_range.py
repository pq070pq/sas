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
