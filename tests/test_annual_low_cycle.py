import unittest

from app.annual_low_cycle import analyze_annual_low_cycle


def candles(count, base=5.0, step=0.001):
    rows = []
    for i in range(count):
        close = base + (i % 7) * step
        rows.append({
            "open": close - 0.01,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "volume": 100000,
        })
    return rows


class AnnualLowCycleTests(unittest.TestCase):
    def test_price_range_is_enforced(self):
        result = analyze_annual_low_cycle("TEST", 10.01, candles(70), candles(35))
        self.assertEqual(result["status"], "out_of_price_range")

    def test_missing_candles_are_not_invented(self):
        result = analyze_annual_low_cycle("TEST", 5.0, [], [])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["nearest_valid_support"])

    def test_reverse_split_cycle_target_hit_is_excluded(self):
        result = analyze_annual_low_cycle(
            "TEST", 5.0, candles(70), candles(35),
            reverse_split_date="2026-01-01",
            reverse_split_verified=True,
            cycle_target_hit_after_split=True,
        )
        self.assertEqual(result["status"], "cycle_target_already_hit")

    def test_unverified_target_after_known_split_needs_review(self):
        result = analyze_annual_low_cycle(
            "TEST", 5.0, candles(70), candles(35),
            reverse_split_date="2026-01-01",
            reverse_split_verified=True,
            cycle_target_hit_after_split=None,
        )
        self.assertEqual(result["status"], "needs_corporate_action_review")

    def test_rsi_is_informational_not_a_filter(self):
        daily = candles(70)
        # Strongly rising daily closes produce high RSI; that must not by itself
        # change the result into a rejection status.
        for i, row in enumerate(daily):
            row["open"] = 2.0 + i * 0.04
            row["low"] = row["open"] - 0.02
            row["close"] = row["open"] + 0.03
            row["high"] = row["close"] + 0.02
        result = analyze_annual_low_cycle("TEST", 5.0, daily, candles(35))
        self.assertIsNotNone(result["daily_rsi"])
        self.assertNotEqual(result["status"], "rsi_rejected")


if __name__ == "__main__":
    unittest.main()
