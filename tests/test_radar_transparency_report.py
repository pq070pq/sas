import unittest

from app.main import build_report


class RadarTransparencyReportTests(unittest.TestCase):
    def test_report_explains_passed_failed_conditions_and_publication_reason(self):
        report = build_report(
            "ABCD",
            {"price": 2.5, "change_pct": 4.2, "source": "test"},
            {
                "radar_checks": {
                    "momentum": True,
                    "sas_core": False,
                    "liquidity": True,
                    "rvol": False,
                    "target": False,
                    "live_levels": True,
                    "no_distribution": True,
                    "no_bearish_hs": True,
                    "no_chase": False,
                    "advanced_confirmation": False,
                    "momentum_rvol": 0.8,
                    "momentum_rvol_threshold": 1.2,
                },
                "channel_gate": {"passed": False, "reason": "تأكيد الحجم غير كافٍ"},
                "radar_candidate_status": "مرشح تحت المراقبة — لم يجتز بوابة التأكيد",
            },
            {"score": 54, "pass": False, "opportunity_status": "watch"},
        )
        self.assertIn("فحص الشروط", report)
        self.assertIn("تحقق:", report)
        self.assertIn("لم يتحقق:", report)
        self.assertIn("سبب الإرسال للمراقبة", report)
        self.assertIn("تأكيد الحجم غير كافٍ", report)
        self.assertIn("RVOL", report)


if __name__ == "__main__":
    unittest.main()
