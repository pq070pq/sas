from app import tradingagents_review as review


def test_shadow_review_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(review.settings, "tradingagents_shadow_review_enabled", False, raising=False)
    result = review.build_shadow_review("xyz", {"enabled": True, "status": "ok"})
    assert result["status"] == "disabled"
    assert result["symbol"] == "XYZ"


def test_shadow_review_filters_unknown_supporting_sources(monkeypatch):
    monkeypatch.setattr(review.settings, "tradingagents_shadow_review_enabled", True, raising=False)
    result = review.build_shadow_review("xyz", {
        "enabled": True, "status": "ok", "primary_source_id": "N1",
        "supporting_source_ids": ["N2", "N9"], "news_assessment": "ارتباط محتمل",
        "momentum_assessment": "متوسط", "risk_flags": ["سيولة منخفضة"],
        "headline_summary": "خبر توضيحي", "key_takeaway": "المعلومات محدودة",
        "dilution_risk": "غير واضح",
    }, evidence_ids={"N1", "N2"})
    assert result["status"] == "ok"
    assert result["mode"] == "shadow_only"
    assert result["evidence"]["supporting_source_ids"] == ["N2"]
    assert "لا يضيف أو يحذف أو يرتب" in result["decision_role"]


def test_shadow_review_rejects_unknown_primary_source(monkeypatch):
    monkeypatch.setattr(review.settings, "tradingagents_shadow_review_enabled", True, raising=False)
    result = review.build_shadow_review("xyz", {
        "enabled": True, "status": "ok", "primary_source_id": "N99",
    }, evidence_ids={"N1"})
    assert result["status"] == "rejected_invalid_evidence"


def test_shadow_review_handles_missing_analysis(monkeypatch):
    monkeypatch.setattr(review.settings, "tradingagents_shadow_review_enabled", True, raising=False)
    result = review.build_shadow_review("xyz", None)
    assert result["status"] == "insufficient_evidence"
    assert result["mode"] == "shadow_only"
