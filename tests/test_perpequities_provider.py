from datetime import datetime, timezone

from app.perpequities_provider import normalize_asset


def _asset(**overrides):
    data = {
        "ticker": "OSS",
        "price": 10.18,
        "change24h": 0.02,
        "change7d": -0.01,
        "qualityFilteredVolume24hUsd": 120000,
        "openInterestUsd": 50000,
        "quality": {
            "volume": {"observedMarkets": 1, "totalMarkets": 2},
            "openInterest": {"observedMarkets": 1, "totalMarkets": 2},
        },
        "url": "https://perpequities.com/stocks/oss-perp",
        "venues": [{
            "venue": "example",
            "status": "trading",
            "live": True,
            "volume24hUsd": 120000,
            "openInterestUsd": 50000,
            "spreadBps": 12,
            "depth1PctUsd": 1000,
            "fundingApr": -0.02,
            "updatedAt": "2026-10-11T11:59:00Z",
        }],
    }
    data.update(overrides)
    return data


def _payload(**overrides):
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    payload = {
        "generatedAt": "2026-10-11T12:00:00Z",
        "marketDataAt": "2026-10-11T11:59:00Z",
        "data": _asset(),
    }
    if "data" in overrides:
        payload["data"] = overrides.pop("data")
    payload.update(overrides)
    return payload, now


def test_normalize_returns_contract_context_and_attribution():
    payload, now = _payload()
    result = normalize_asset(payload, "OSS", now)
    assert result is not None
    assert result["reference_price"] == 10.18
    assert result["live_markets"] == 1
    assert result["venues"][0]["funding_apr"] == -0.02
    assert result["is_contract_context_only"] is True
    assert result["attribution"] == "Data: PerpEquities (perpequities.com)"


def test_normalize_finds_symbol_in_bulk_snapshot():
    payload, now = _payload(data=[{"ticker": "AAPL", "price": 200}, _asset()])
    result = normalize_asset(payload, "OSS", now)
    assert result is not None
    assert result["symbol"] == "OSS"


def test_normalize_rejects_wrong_ticker():
    payload, now = _payload(data={"ticker": "AAPL"})
    assert normalize_asset(payload, "OSS", now) is None


def test_normalize_rejects_missing_market_timestamp():
    payload, now = _payload(marketDataAt=None)
    assert normalize_asset(payload, "OSS", now) is None


def test_normalize_rejects_stale_snapshot():
    payload, now = _payload(stale=True)
    assert normalize_asset(payload, "OSS", now) is None


def test_normalize_rejects_old_market_data():
    payload, now = _payload(marketDataAt="2026-10-11T11:00:00Z")
    assert normalize_asset(payload, "OSS", now) is None


def test_normalize_keeps_unknown_aggregates_as_none():
    payload, now = _payload(data={"ticker": "OSS", "qualityFilteredVolume24hUsd": None, "openInterestUsd": None})
    result = normalize_asset(payload, "OSS", now)
    assert result is not None
    assert result["volume_24h_usd"] is None
    assert result["open_interest_usd"] is None
