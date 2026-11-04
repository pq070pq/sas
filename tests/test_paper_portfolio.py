import unittest

from app.paper_portfolio import (
    calculate_position_size,
    resolve_long_barrier,
    summarize_closed_trades,
)


class PaperPortfolioSizingTests(unittest.TestCase):
    def test_position_size_obeys_risk_budget_and_notional_cap(self):
        result = calculate_position_size(
            equity=10000, entry_price=2.0, stop_price=1.8,
            risk_fraction=0.01, max_position_fraction=0.20,
        )
        self.assertEqual(result["shares"], 500)
        self.assertAlmostEqual(result["planned_risk"], 100.0)
        self.assertAlmostEqual(result["notional"], 1000.0)

    def test_position_size_rejects_invalid_long_levels(self):
        with self.assertRaises(ValueError):
            calculate_position_size(equity=10000, entry_price=2, stop_price=2)
        with self.assertRaises(ValueError):
            calculate_position_size(equity=10000, entry_price=2, stop_price=1, risk_fraction=2)


class PaperPortfolioExecutionTests(unittest.TestCase):
    def test_no_barrier_touch_returns_no_fill(self):
        self.assertIsNone(resolve_long_barrier(
            open_price=2.0, high_price=2.1, low_price=1.9,
            stop_price=1.8, target_price=2.2,
        ))

    def test_stop_wins_when_stop_and_target_are_both_touched(self):
        result = resolve_long_barrier(
            open_price=2.0, high_price=2.3, low_price=1.7,
            stop_price=1.8, target_price=2.2,
        )
        self.assertEqual(result["event"], "stop")
        self.assertEqual(result["fill_price"], 1.8)

    def test_gap_below_stop_uses_worse_observed_open(self):
        result = resolve_long_barrier(
            open_price=1.6, high_price=1.9, low_price=1.5,
            stop_price=1.8, target_price=2.2,
        )
        self.assertEqual(result["fill_price"], 1.6)

    def test_target_fill_does_not_claim_unverified_price_improvement(self):
        result = resolve_long_barrier(
            open_price=2.3, high_price=2.4, low_price=2.1,
            stop_price=1.8, target_price=2.2,
        )
        self.assertEqual(result["event"], "target")
        self.assertEqual(result["fill_price"], 2.2)


class PaperPortfolioPerformanceTests(unittest.TestCase):
    def test_metrics_use_closed_trades_only(self):
        result = summarize_closed_trades([
            {"status": "closed", "pnl": 100},
            {"status": "lost", "pnl": -40},
            {"status": "active", "pnl": 999},
        ], starting_equity=10000)
        self.assertEqual(result["closed_trades"], 2)
        self.assertEqual(result["win_rate_pct"], 50.0)
        self.assertEqual(result["net_pnl"], 60.0)
        self.assertEqual(result["ending_equity"], 10060.0)
        self.assertEqual(result["profit_factor"], 2.5)

    def test_empty_history_has_undefined_rates_not_fake_zero_success(self):
        result = summarize_closed_trades([], starting_equity=10000)
        self.assertIsNone(result["win_rate_pct"])
        self.assertIsNone(result["profit_factor"])
        self.assertEqual(result["closed_trades"], 0)


if __name__ == "__main__":
    unittest.main()
