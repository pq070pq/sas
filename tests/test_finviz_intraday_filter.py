import unittest

from app.finviz_intraday_filter import evaluate_intraday_candidate


class FinvizIntradayFilterTests(unittest.TestCase):
    def test_candidate_passes_all_thresholds(self):
        result = evaluate_intraday_candidate(
            price=5.50,
            float_shares=12_000_000,
            volume=800_000,
            session_open=5.00,
            analyst_target=8.25,
            analyst_count=3,
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["change_from_open_pct"], 10.0)
        self.assertEqual(result["reasons"], [])

    def test_target_below_fifty_percent_fails_closed(self):
        result = evaluate_intraday_candidate(
            price=4.00, float_shares=10_000_000, volume=900_000, session_open=3.60,
            analyst_target=5.99, analyst_count=5,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("متوسط هدف المحللين أقل من 50% فوق السعر الحالي", result["reasons"])

    def test_insufficient_analyst_coverage_fails_closed(self):
        result = evaluate_intraday_candidate(
            price=4.00, float_shares=10_000_000, volume=900_000, session_open=3.60,
            analyst_target=7.00, analyst_count=2,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("عدد المحللين المؤكدين أقل من 3 أو غير متوفر", result["reasons"])

    def test_daily_gain_is_not_substituted_for_change_from_open(self):
        result = evaluate_intraday_candidate(
            price=5.40,
            float_shares=12_000_000,
            volume=800_000,
            session_open=5.00,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("الارتفاع من افتتاح الجلسة أقل من 10%", result["reasons"])

    def test_missing_float_fails_closed(self):
        result = evaluate_intraday_candidate(
            price=5.50,
            float_shares=None,
            volume=800_000,
            session_open=5.00,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("عدد الأسهم الحرة غير متوفر؛ يلزم التحقق", result["reasons"])

    def test_threshold_boundaries_are_strict_where_requested(self):
        result = evaluate_intraday_candidate(
            price=1.00,
            float_shares=19_999_999,
            volume=500_000,
            session_open=0.90,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("حجم التداول لا يتجاوز 500 ألف سهم", result["reasons"])


if __name__ == "__main__":
    unittest.main()
