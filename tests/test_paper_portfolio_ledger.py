import unittest

from app.paper_portfolio_ledger import prepare_paper_trade


class PaperPortfolioLedgerTests(unittest.TestCase):
    def test_prepares_trade_only_from_confirmed_gate_passed_signal(self):
        trade = prepare_paper_trade(
            radar_signal_id=17, symbol="abc", session_date="2026-11-04",
            channel_gate_passed=True, confirmed=True,
            entry_price=2.0, stop_price=1.8, targets=[2.2, 2.4, 2.6, 2.8, 3.0],
            equity=10000,
        )
        self.assertEqual(trade["symbol"], "ABC")
        self.assertEqual(trade["shares"], 500)
        self.assertEqual(trade["target1"], 2.2)
        self.assertEqual(trade["target5"], 3.0)
        self.assertEqual(trade["currency"], "USD")

    def test_rejects_watch_or_rejected_signal(self):
        with self.assertRaises(ValueError):
            prepare_paper_trade(
                radar_signal_id=17, symbol="ABC", session_date="2026-11-04",
                channel_gate_passed=False, confirmed=False,
                entry_price=2.0, stop_price=1.8, targets=[2.2], equity=10000,
            )

    def test_rejects_missing_market_levels(self):
        with self.assertRaises(ValueError):
            prepare_paper_trade(
                radar_signal_id=17, symbol="ABC", session_date="2026-11-04",
                channel_gate_passed=True, confirmed=True,
                entry_price=2.0, stop_price=None, targets=[2.2], equity=10000,
            )

    def test_rejects_currency_mismatch_instead_of_mixing_sar_and_usd(self):
        with self.assertRaises(ValueError):
            prepare_paper_trade(
                radar_signal_id=17, symbol="ABC", session_date="2026-11-04",
                channel_gate_passed=True, confirmed=True,
                entry_price=2.0, stop_price=1.8, targets=[2.2], equity=10000,
                currency="SAR",
            )

    def test_rejects_non_finite_prices(self):
        with self.assertRaises(ValueError):
            prepare_paper_trade(
                radar_signal_id=17, symbol="ABC", session_date="2026-11-04",
                channel_gate_passed=True, confirmed=True,
                entry_price=float("nan"), stop_price=1.8, targets=[2.2], equity=10000,
            )

    def test_rejects_target_not_above_entry(self):
        with self.assertRaises(ValueError):
            prepare_paper_trade(
                radar_signal_id=17, symbol="ABC", session_date="2026-11-04",
                channel_gate_passed=True, confirmed=True,
                entry_price=2.0, stop_price=1.8, targets=[1.9], equity=10000,
            )


if __name__ == "__main__":
    unittest.main()
