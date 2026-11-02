import unittest

from app.speculative_radar import (
    _radar_session_is_scannable,
    build_speculative_report,
    speculative_report_key,
    speculative_expiry_reason,
    speculative_exclusion_reason,
)
from app.finviz_intraday_filter import evaluate_intraday_candidate


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
        self.assertIn("SAS PRO | أسهم المضاربي اللحظي", report)
        self.assertIn("+10.00%", report)

    def test_price_range_includes_1_and_10_dollars(self):
        for price, session_open in ((1.00, 0.90), (10.00, 9.00)):
            with self.subTest(price=price):
                result = evaluate_intraday_candidate(
                    price=price,
                    float_shares=10_000_000,
                    volume=600_000,
                    session_open=session_open,
                )
                self.assertTrue(result["eligible"], result["reasons"])

    def test_price_outside_range_is_rejected(self):
        for price in (0.99, 10.01):
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

    def test_radar_runs_only_in_sessions_with_supported_live_quotes(self):
        for session in ("premarket", "regular", "afterhours", "night"):
            with self.subTest(session=session):
                self.assertTrue(_radar_session_is_scannable({"session": session, "holiday": False}))

    def test_radar_skips_transition_and_unsupported_sessions(self):
        for session in ("night_pending", "overnight", "weekend", "holiday", ""):
            with self.subTest(session=session):
                self.assertFalse(_radar_session_is_scannable({"session": session, "holiday": False}))

    def test_radar_skips_all_sessions_on_market_holidays(self):
        for session in ("premarket", "regular", "afterhours", "night"):
            with self.subTest(session=session):
                self.assertFalse(_radar_session_is_scannable({"session": session, "holiday": True}))

    def test_missing_symbol_is_rejected(self):
        report, result = build_speculative_report({"price": 5.5})
        self.assertIsNone(report)
        self.assertFalse(result["eligible"])


    def test_speculative_opportunity_expires_only_after_session_date_rollover(self):
        self.assertIsNone(speculative_expiry_reason("2026-11-03", "2026-11-03"))
        self.assertEqual(
            speculative_expiry_reason("2026-11-02", "2026-11-03"),
            "انتهت جلسة الرصد",
        )
        self.assertIsNone(speculative_expiry_reason("", "2026-11-03"))



    def test_excludes_only_when_fresh_observation_breaks_a_known_rule(self):
        self.assertEqual(
            speculative_exclusion_reason(price=0.95, volume=700_000, session_open=0.90, quote_fresh=True),
            "خرج السعر من النطاق المحدد: $0.9500",
        )
        self.assertEqual(
            speculative_exclusion_reason(price=5.2, volume=400_000, session_open=4.7, quote_fresh=True),
            "هبط حجم التداول إلى 400,000 سهم أو أقل من الحد المطلوب",
        )
        self.assertIn(
            "فقد شرط الزخم",
            speculative_exclusion_reason(price=5.0, volume=700_000, session_open=4.8, quote_fresh=True),
        )

    def test_does_not_exclude_on_missing_or_stale_observation(self):
        self.assertIsNone(
            speculative_exclusion_reason(price=0.90, volume=100_000, session_open=1.0, quote_fresh=False)
        )
        self.assertIsNone(
            speculative_exclusion_reason(price=0.90, volume=None, session_open=1.0, quote_fresh=True)
        )
        self.assertIsNone(
            speculative_exclusion_reason(price=5.5, volume=700_000, session_open=5.0, quote_fresh=True)
        )



if __name__ == "__main__":
    unittest.main()
