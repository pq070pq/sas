import unittest

from app.speculative_radar import build_speculative_report, speculative_report_key


class SpeculativeRadarTests(unittest.TestCase):
    def test_valid_candidate_builds_report(self):
        report, result = build_speculative_report({
            "symbol": "ABCD",
            "price": 5.5,
            "float_shares": 12_000_000,
            "volume": 800_000,
            "session_open": 5.0,
        })
        self.assertTrue(result["eligible"])
        self.assertIn("SAS PRO | رصد الأسهم المضاربية", report)
        self.assertIn("+10.00%", report)

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
