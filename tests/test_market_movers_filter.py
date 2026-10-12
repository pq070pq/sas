from app.main import _movers_price_in_range, _rank_perpequities_movers


def test_movers_price_filter_includes_range_edges_and_excludes_outside():
    assert _movers_price_in_range({"price": 0.30})
    assert _movers_price_in_range({"price": 20})
    assert not _movers_price_in_range({"price": 0.29})
    assert not _movers_price_in_range({"price": 20.01})
    assert not _movers_price_in_range({"price": None})
    assert not _movers_price_in_range({"price": "invalid"})


def test_movers_ranking_filters_prices_and_orders_both_sides():
    assets = [
        {"symbol": "LOW", "price": 0.29, "change24h": 0.90},
        {"symbol": "GAIN2", "price": 3.20, "change24h": 0.15},
        {"symbol": "GAIN1", "price": 20.00, "change24h": 0.22},
        {"symbol": "HIGH", "price": 20.01, "change24h": 0.99},
        {"symbol": "LOSS2", "price": 4.00, "change24h": -0.07},
        {"symbol": "LOSS1", "price": 0.30, "change24h": -0.18},
        {"symbol": "NOCHANGE", "price": 7.00, "change24h": 0},
        {"symbol": "NOPRICE", "change24h": 0.80},
    ]
    gainers, losers, eligible = _rank_perpequities_movers(assets)
    assert [x["symbol"] for x in gainers] == ["GAIN1", "GAIN2"]
    assert [x["symbol"] for x in losers] == ["LOSS1", "LOSS2"]
    assert {x["symbol"] for x in eligible} == {"GAIN1", "GAIN2", "LOSS1", "LOSS2", "NOCHANGE"}
    assert all(0.30 <= x["price"] <= 20.00 for x in eligible)
    assert gainers[0]["change_pct"] == 22.0


def test_movers_returns_all_eligible_gainers_and_losers_not_only_top_eight():
    assets = []
    for i in range(12):
        assets.append({"symbol": f"UP{i:02d}", "price": 1 + i / 10, "change24h": (i + 1) / 100})
        assets.append({"symbol": f"DN{i:02d}", "price": 2 + i / 10, "change24h": -(i + 1) / 100})
    gainers, losers, eligible = _rank_perpequities_movers(assets)
    assert len(gainers) == 12
    assert len(losers) == 12
    assert gainers[0]["symbol"] == "UP11"
    assert losers[0]["symbol"] == "DN11"
    assert len(eligible) == 24
