import unittest

from app.radar_confirmation import confirmation_state


class RadarConfirmationTests(unittest.TestCase):
    def test_target_is_optional_when_strategy_live_price_and_stop_pass(self):
        state = confirmation_state(strategy_pass=True, live_price=2.00,
            live_source="Live Quote", targets={"status": "watch", "targets": [], "exit": 1.94})
        self.assertTrue(state["confirmation_ready"])
        self.assertEqual(state["opportunity_status"], "confirmed")
        self.assertFalse(state["target_confirmed"])

    def test_missing_stop_keeps_candidate_on_watch(self):
        state = confirmation_state(strategy_pass=True, live_price=2.00,
            live_source="Live Quote", targets={"status": "watch", "targets": []})
        self.assertFalse(state["confirmation_ready"])
        self.assertEqual(state["opportunity_status"], "watch")

    def test_too_tight_stop_keeps_candidate_on_watch(self):
        state = confirmation_state(strategy_pass=True, live_price=2.00,
            live_source="Live Quote", targets={"exit": 1.99})
        self.assertFalse(state["stop_ready"])
        self.assertFalse(state["confirmation_ready"])

    def test_untrusted_live_source_keeps_candidate_on_watch(self):
        state = confirmation_state(strategy_pass=True, live_price=2.00,
            live_source="scan data", targets={"exit": 1.94})
        self.assertFalse(state["live_price_ready"])
        self.assertFalse(state["confirmation_ready"])

    def test_strategy_failure_cannot_become_confirmed(self):
        state = confirmation_state(strategy_pass=False, live_price=2.00,
            live_source="Live Quote", targets={"exit": 1.94})
        self.assertFalse(state["confirmation_ready"])


if __name__ == "__main__":
    unittest.main()
