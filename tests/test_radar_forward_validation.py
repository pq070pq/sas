from app.radar_forward_validation import _close_map, _pct, _aligned_forward_days


def test_close_map_uses_real_daily_close_and_ignores_bad_rows():
    result = {"candles": [
        {"time": "2026-10-01", "close": 10},
        {"time": "2026-10-02", "close": "11.5"},
        {"time": "2026-10-03", "close": None},
    ]}
    assert _close_map(result) == {"2026-10-01": 10.0, "2026-10-02": 11.5}


def test_pct_calculates_forward_return():
    assert round(_pct(100, 110), 5) == 10.0
    assert round(_pct(100, 90), 5) == -10.0
    assert _pct(None, 100) is None
    assert _pct(0, 100) is None


def test_missing_history_is_empty_not_a_win():
    assert _close_map({"candles": []}) == {}


def test_forward_sessions_are_aligned_between_stock_and_spy():
    stock = {"2026-10-02": 11, "2026-10-05": 12, "2026-10-06": 13}
    spy = {"2026-10-02": 101, "2026-10-06": 103, "2026-10-07": 104}
    assert _aligned_forward_days(stock, spy, "2026-10-01") == ["2026-10-02", "2026-10-06"]


def test_close_map_excludes_current_incomplete_session():
    result = {"candles": [
        {"time": "2026-10-09", "close": 10},
        {"time": "2026-10-12", "close": 11},
    ]}
    assert _close_map(result, before_day="2026-10-12") == {"2026-10-09": 10.0}
