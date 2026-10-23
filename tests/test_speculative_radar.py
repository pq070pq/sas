import unittest

from app.speculative_radar import build_speculative_report, speculative_report_key
from app.finviz_intraday_filter import evaluate_intraday_candidate


class SpeculativeRadarTests(unittest.TestCase):
    def test_valid_candidate_builds_report(self):
        report, result = build_speculative_report({
            "symbol": "ABCD",
            "price": 5.5,
            "float_shares": 12_000_000,
            "volume": 800_000,
            "session_open": 5.0,
            "analyst_target": 8.25,
            "analyst_count": 3,
        })
        self.assertTrue(result["eligible"])
        self.assertIn("SAS PRO | المضاربي اللحظي + أهداف المحللين", report)
        self.assertIn("هدف المحللين", report)
        self.assertIn("+10.00%", report)

    def test_price_range_includes_1_and_10_dollars(self):
        for price, session_open in ((0.30, 0.27), (6.00, 5.40)):
            with self.subTest(price=price):
                result = evaluate_intraday_candidate(
                    price=price,
                    float_shares=10_000_000,
                    volume=600_000,
                    session_open=session_open,
                    analyst_target=price * 1.5,
                    analyst_count=3,
                )
                self.assertTrue(result["eligible"], result["reasons"])

    def test_price_outside_range_is_rejected(self):
        for price in (0.29, 6.01):
            with self.subTest(price=price):
                result = evaluate_intraday_candidate(
                    price=price,
                    float_shares=10_000_000,
                    volume=600_000,
                    session_open=price / 1.10,
                )
                self.assertFalse(result["eligible"])

    def test_missing_float_never_builds_eligible_report(self):
        report, result = build_speculative_report({
            "symbol": "ABCD",
            "price": 5.5,
            "float_shares": None,
            "volume": 800_000,
            "session_open": 5.0,
        })
        self.assertIsNone(report)
        self.assertFalse(result["eligible"])

    def test_deduplication_key_normalizes_symbol_and_session(self):
        self.assertEqual(
            speculative_report_key(" abcd ", "2026-10-21"),
            speculative_report_key("ABCD", "2026-10-21"),
        )
        self.assertNotEqual(
            speculative_report_key("ABCD", "2026-10-21"),
            speculative_report_key("ABCD", "2026-10-22"),
        )

    def test_missing_symbol_is_rejected(self):
        report, result = build_speculative_report({"price": 5.5})
        self.assertIsNone(report)
        self.assertFalse(result["eligible"])


if __name__ == "__main__":
    unittest.main()
