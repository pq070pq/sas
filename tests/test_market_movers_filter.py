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



def test_finviz_screener_parses_unique_gainers_with_price_band():
    from app.scanner import _parse_finviz_screener
    html = """
    <table><tr><td>Ticker</td><td>Company</td><td>Price</td><td>Change</td><td>Volume</td></tr>
    <tr><td>ABCD</td><td>Alpha</td><td>4.25</td><td>+8.10%</td><td>1,200,000</td></tr>
    <tr><td>ABCD</td><td>Alpha duplicate</td><td>4.25</td><td>+8.10%</td><td>1,200,000</td></tr>
    <tr><td>CHEAP</td><td>Too cheap</td><td>0.20</td><td>+30.00%</td><td>900,000</td></tr>
    <tr><td>EDGELOW</td><td>Lower edge</td><td>0.30</td><td>+6.00%</td><td>900,000</td></tr>
    <tr><td>EDGEHIGH</td><td>Upper edge</td><td>6.00</td><td>+7.00%</td><td>900,000</td></tr>
    <tr><td>EXPENSIVE</td><td>Too expensive</td><td>6.01</td><td>+5.00%</td><td>900,000</td></tr>
    <tr><td>RED</td><td>Loser</td><td>3.00</td><td>-4.00%</td><td>700,000</td></tr>
    </table>
    """
    rows = _parse_finviz_screener(html, "gainers")
    assert [x["symbol"] for x in rows] == ["ABCD", "EDGELOW", "EDGEHIGH"]
    assert rows[0]["change_pct"] == 8.1
    assert rows[0]["source"] == "Finviz"


def test_finviz_screener_parses_losers_and_deduplicates_symbols():
    from app.scanner import _parse_finviz_screener
    html = """
    <table><tr><td>Ticker</td><td>Company</td><td>Price</td><td>Change</td><td>Volume</td></tr>
    <tr><td>LOSS</td><td>Loser</td><td>2.75</td><td>-3.25%</td><td>850,000</td></tr>
    <tr><td>WIN</td><td>Winner</td><td>2.75</td><td>+3.25%</td><td>850,000</td></tr>
    <tr><td>LOSS</td><td>Duplicate</td><td>2.75</td><td>-3.25%</td><td>850,000</td></tr>
    </table>
    """
    rows = _parse_finviz_screener(html, "losers")
    assert [x["symbol"] for x in rows] == ["LOSS"]
    assert rows[0]["change_pct"] == -3.25
