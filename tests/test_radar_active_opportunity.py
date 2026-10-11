from app.radar_policy import is_confirmed_opportunity


def test_only_fully_confirmed_stock_is_an_active_saved_opportunity():
    assert is_confirmed_opportunity({
        "classification": {
            "opportunity_status": "confirmed",
            "confirmation_ready": True,
        }
    })


def test_watch_or_failed_confirmation_is_not_an_active_saved_opportunity():
    assert not is_confirmed_opportunity({
        "classification": {
            "opportunity_status": "watch",
            "confirmation_ready": False,
        }
    })
    assert not is_confirmed_opportunity({
        "classification": {
            "opportunity_status": "confirmed",
            "confirmation_ready": False,
        }
    })


def test_missing_or_invalid_classification_is_not_an_active_opportunity():
    assert not is_confirmed_opportunity({})
    assert not is_confirmed_opportunity({"classification": None})
    assert not is_confirmed_opportunity({"classification": "confirmed"})
    assert not is_confirmed_opportunity(None)
