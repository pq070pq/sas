import unittest

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


if __name__ == "__main__":
    unittest.main()
