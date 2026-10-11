from app.radar_forward_validation import _close_map, _pct


def test_close_map_uses_real_daily_close_and_ignores_bad_rows():
    result = {"candles": [
        {"time": "2026-10-01", "close": 10},
        {"time": "2026-10-02", "close": "11.5"},
        {"time": "2026-10-03", "close": None},
    ]}
    assert _close_map(result) == {"2026-10-01": 10.0, "2026-10-02": 11.5}


def test_pct_calculates_forward_return():
    assert _pct(100, 110) == 10.000000000000009
    assert _pct(100, 90) == -9.999999999999998
    assert _pct(None, 100) is None
    assert _pct(0, 100) is None


def test_missing_history_is_empty_not_a_win():
    assert _close_map({"candles": []}) == {}
