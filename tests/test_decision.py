import pytest

import decision
from decision import MIN_DONATION_KG, MIN_SAFE_HOURS, classify_risk, decide


@pytest.mark.parametrize("kg,expected", [
    (0, "LOW"), (4.9, "LOW"), (5.0, "MEDIUM"), (10, "MEDIUM"), (15.0, "MEDIUM"),
    (15.1, "HIGH"), (80, "HIGH"),
])
def test_risk_classification(kg, expected):
    assert classify_risk(kg) == expected


def test_thresholds_are_configurable(monkeypatch):
    monkeypatch.setattr(decision, "RISK_LOW_MAX", 10.0)
    assert classify_risk(7) == "LOW"


def test_result_structure():
    r = decide(10, 8)
    assert set(r) == {"decision", "reason", "risk", "warning", "donate_kg"}


def test_low_risk_suitable_food_is_donated():
    r = decide(3.0, 8)
    assert r["decision"] == "DONATE" and r["risk"] == "LOW" and r["warning"] is None


def test_medium_risk_is_donated():
    r = decide(10.0, 8)
    assert r["decision"] == "DONATE" and r["risk"] == "MEDIUM"


def test_high_risk_is_donated_with_warning():
    r = decide(40.0, 8)
    assert r["decision"] == "DONATE" and r["risk"] == "HIGH"
    assert r["warning"] and "quality" in r["warning"]


def test_expired_food_is_never_donated():
    r = decide(50, 0)
    assert r["decision"] == "DO_NOT_DONATE" and "expired" in r["reason"]
    assert r["donate_kg"] == 0


def test_negative_hours_treated_as_expired():
    assert decide(50, -5)["decision"] == "DO_NOT_DONATE"


def test_short_shelf_life_not_donated():
    r = decide(50, MIN_SAFE_HOURS - 0.5)
    assert r["decision"] == "DO_NOT_DONATE" and "shelf life" in r["reason"]


def test_insufficient_quantity_is_disposed():
    r = decide(MIN_DONATION_KG - 0.5, 10)
    assert r["decision"] == "DISPOSE" and "minimum" in r["reason"]


def test_expiry_checked_before_quantity():
    assert decide(0.5, 0)["decision"] == "DO_NOT_DONATE"


def test_boundaries_are_inclusive():
    assert decide(MIN_DONATION_KG, MIN_SAFE_HOURS)["decision"] == "DONATE"


def test_ngo_capacity_limits_donation():
    r = decide(50, 10, ngo_capacity_kg=30)
    assert r["decision"] == "DONATE" and r["donate_kg"] == 30
    assert "another NGO" in r["reason"]


def test_ngo_with_enough_capacity_takes_everything():
    assert decide(20, 10, ngo_capacity_kg=100)["donate_kg"] == 20
