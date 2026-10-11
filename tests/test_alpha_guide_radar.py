from datetime import datetime
from zoneinfo import ZoneInfo

from app.alpha_guide import evaluate_alpha_guide


def candles(count=230, breakout=False, volume_spike=False):
    rows = []
    for i in range(count):
        close = 10.0 + i * 0.03
        if breakout and i == count - 1:
            close += 0.20
        if breakout and i == count - 2:
            close += 0.20
        volume = 1000.0
        if volume_spike and i == count - 1:
            volume = 3000.0
        rows.append({
            "open": close - 0.02,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": volume,
        })
    return rows


def test_alpha_guide_requires_200_completed_bars():
    result = evaluate_alpha_guide(candles(100), spy_return_20=-0.01)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["checks"] == {}


def test_alpha_guide_reports_watch_when_trend_passes_but_no_breakout():
    result = evaluate_alpha_guide(candles(230), spy_return_20=-0.05)
    assert result["status"] == "WATCH"
    assert result["checks"]["ema_stack_20_50_200"] is True
    assert result["checks"]["rsi_at_least_55"] is True
    assert result["checks"]["close_above_previous_20d_high"] is False


def test_alpha_guide_reports_breakout_only_when_volume_and_close_confirm():
    rows = candles(230, breakout=True, volume_spike=True)
    # Make the current close clearly exceed the prior 20-session high.
    rows[-1]["close"] = rows[-2]["close"] + 0.50
    rows[-1]["high"] = rows[-1]["close"] + 0.02
    rows[-1]["volume"] = 3000.0
    result = evaluate_alpha_guide(rows, spy_return_20=-0.05)
    assert result["status"] in {"BREAKOUT", "CONFIRMED"}
    assert result["checks"]["close_above_previous_20d_high"] is True
    assert result["checks"]["rvol20_at_least_2"] is True
