from app.radar_state import is_confirmed_opportunity, mark_opportunity_inactive


def test_only_current_confirmed_and_ready_rows_count_as_opportunities():
    assert is_confirmed_opportunity({
        "classification": {"opportunity_status": "confirmed", "confirmation_ready": True}
    })
    assert not is_confirmed_opportunity({
        "classification": {"opportunity_status": "watch", "confirmation_ready": False}
    })
    assert not is_confirmed_opportunity({
        "classification": {"opportunity_status": "confirmed", "confirmation_ready": False}
    })
    assert not is_confirmed_opportunity(None)


def test_inactivating_saved_opportunity_preserves_history_and_reason():
    original = {
        "symbol": "TEST",
        "radar_active": True,
        "telegram_message_id": 123,
        "historical_outcome": {"target_hit": 1},
        "channel_gate": {"passed": True, "session": "regular"},
    }
    updated = mark_opportunity_inactive(
        original, "فقد شروط الفرصة المؤكدة في الدورة الحالية", "regular"
    )
    assert updated["radar_active"] is False
    assert updated["strategy_status"] == "inactive"
    assert updated["channel_gate"]["passed"] is False
    assert updated["channel_gate"]["session"] == "regular"
    assert updated["telegram_message_id"] == 123
    assert updated["historical_outcome"] == {"target_hit": 1}
    assert original["radar_active"] is True
