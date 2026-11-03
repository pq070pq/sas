from app.radar_lifecycle import is_current_active_opportunity


def make_payload(**overrides):
    payload = {
        "radar_active": True,
        "channel_gate": {"passed": True},
        "classification": {
            "opportunity_status": "confirmed",
            "confirmation_ready": True,
        },
    }
    payload.update(overrides)
    return payload


def test_only_current_confirmed_active_opportunities_are_saved():
    assert is_current_active_opportunity(make_payload())


def test_inactive_or_unconfirmed_opportunities_are_excluded():
    assert not is_current_active_opportunity(make_payload(radar_active=False))
    assert not is_current_active_opportunity(make_payload(channel_gate={"passed": False}))
    assert not is_current_active_opportunity(make_payload(classification={
        "opportunity_status": "watch", "confirmation_ready": False
    }))
    assert not is_current_active_opportunity(make_payload(classification={
        "opportunity_status": "confirmed", "confirmation_ready": False
    }))
    assert not is_current_active_opportunity(None)


from datetime import date

from app.radar_lifecycle import is_within_monitoring_window


def test_monitoring_window_accepts_recent_sessions():
    assert is_within_monitoring_window("2026-11-03", "2026-11-03")
    assert is_within_monitoring_window("2026-10-27", "2026-11-03")


def test_monitoring_window_rejects_stale_future_and_invalid_sessions():
    assert not is_within_monitoring_window("2026-10-26", "2026-11-03")
    assert not is_within_monitoring_window("2026-11-04", "2026-11-03")
    assert not is_within_monitoring_window("not-a-date", "2026-11-03")
