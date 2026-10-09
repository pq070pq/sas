import unittest

from app.jobs import _radar_condition_lines, _radar_diagnostic_message


class RadarChannelDiagnosticsTests(unittest.TestCase):
    def test_condition_lines_show_pass_and_fail(self):
        row = {
            "symbol": "ABCD",
            "radar_checks": {"momentum": True, "sas_core": False, "target": True},
            "classification": {"opportunity_status": "watch", "confirmation_ready": False},
        }
        lines = _radar_condition_lines(
            row, {"price": 1.25, "source": "live", "stale": False},
            gate_passed=False, gate_reason="التأكيد غير مكتمل"
        )
        rendered = "\n".join(lines)
        self.assertIn("زخم السهم", rendered)
        self.assertIn("تحقق", rendered)
        self.assertIn("لم يتحقق", rendered)
        self.assertIn("التأكيد غير مكتمل", rendered)

    def test_diagnostic_message_is_not_called_confirmed_signal(self):
        message = _radar_diagnostic_message(
            {"symbol": "ABCD", "radar_checks": {"momentum": True},
             "classification": {"opportunity_status": "watch", "confirmation_ready": False}},
            {"price": 1.25, "change_pct": 4.2, "source": "live"},
            "الفرصة ما زالت تحت المراقبة"
        )
        self.assertIn("رصد وتشخيص", message)
        self.assertIn("ليس إشارة دخول مؤكدة", message)
        self.assertIn("الفرصة ما زالت تحت المراقبة", message)
        self.assertIn("ABCD", message)


if __name__ == "__main__":
    unittest.main()
