import unittest

from app.jobs import _radar_criteria_explanation


class RadarCriteriaExplanationTests(unittest.TestCase):
    def test_explains_pass_fail_unknown_and_gate_reason(self):
        row = {
            "symbol": "TEST",
            "price": 2.5,
            "change_pct": 4.2,
            "quality_score": {"score": 78},
            "classification": {
                "score": 70,
                "pass": True,
                "opportunity_status": "watch",
                "confirmation_ready": False,
            },
            "radar_checks": {
                "momentum": True,
                "sas_core": True,
                "liquidity": True,
                "rvol": False,
                "target": False,
                "live_levels": True,
                "no_distribution": True,
                "no_bearish_hs": True,
                "no_chase": False,
                "risk_reward": 1.2,
            },
        }
        text = _radar_criteria_explanation(
            row, {"price": 2.5, "change_pct": 4.2}, False, "السعر الحي غير متاح"
        )
        self.assertIn("الشروط التي تحققت", text)
        self.assertIn("الشروط التي لم تتحقق", text)
        self.assertIn("غير محسوم / بيانات غير متاحة", text)
        self.assertIn("السعر الحي غير متاح", text)
        self.assertIn("لم يجتزها", text)
        self.assertIn("RVOL", text)

    def test_missing_checks_are_not_reported_as_passed(self):
        text = _radar_criteria_explanation(
            {"symbol": "TEST", "classification": {}}, {}, True, "تم اجتياز البوابة"
        )
        self.assertIn("لا توجد شروط اجتياز مستقلة موثقة", text)
        self.assertIn("غير محددة", text)


if __name__ == "__main__":
    unittest.main()
