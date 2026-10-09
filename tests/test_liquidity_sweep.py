import unittest

from app.liquidity_sweep import detect_liquidity_sweep


def candles(rows):
    return [
        {"open": o, "high": h, "low": l, "close": c, "volume": v}
        for o, h, l, c, v in rows
    ]


class LiquiditySweepTests(unittest.TestCase):
    def test_bullish_sweep_requires_reclaim_and_strong_close(self):
        base = [(10.2, 10.5, 10.0, 10.3, 1000)] * 20
        base[-1] = (10.2, 10.4, 9.95, 10.25, 1200)
        result = detect_liquidity_sweep(candles(base), lookback=19)
        self.assertTrue(result["bullish"])
        self.assertEqual(result["reason"], "bullish_reclaim")

    def test_low_break_without_reclaim_is_not_bullish(self):
        base = [(10.2, 10.5, 10.0, 10.3, 1000)] * 20
        base[-1] = (10.2, 10.4, 9.95, 9.98, 1200)
        result = detect_liquidity_sweep(candles(base), lookback=19)
        self.assertFalse(result["bullish"])

    def test_no_lookahead_uses_only_previous_bars_for_level(self):
        base = [(10.2, 10.5, 10.0, 10.3, 1000)] * 20
        base[-1] = (10.2, 10.4, 9.95, 10.25, 1200)
        result = detect_liquidity_sweep(candles(base), lookback=19)
        self.assertAlmostEqual(result["support_level"], 10.0)

    def test_insufficient_data_returns_safe_no_signal(self):
        result = detect_liquidity_sweep(candles([(1, 1.1, .9, 1, 100)] * 5), lookback=20)
        self.assertFalse(result["bullish"])
        self.assertEqual(result["reason"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()

