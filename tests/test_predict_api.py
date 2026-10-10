"""/predict response: distinct quantities, interval, warnings, validation."""
import pytest

from test_app import PREDICT


def test_quantities_are_distinct_and_consistent(client):
    r = client.post("/predict", json=dict(PREDICT, edible_percent=50)).get_json()
    assert r["edible_percent"] == 50
    assert r["edible_kg"] == round(r["waste_kg"] * 0.5, 1)
    assert r["donate_kg"] <= r["edible_kg"] <= r["waste_kg"]
    low, high = r["interval"]
    assert low <= r["waste_kg"] <= high and r["interval_level"] == 0.9
    assert r["model_name"] and r["input_warnings"] == []


def test_default_is_all_waste_edible(client):
    r = client.post("/predict", json=PREDICT).get_json()
    assert r["edible_kg"] == r["waste_kg"]


def test_zero_edible_share_means_nothing_to_donate(client):
    r = client.post("/predict", json=dict(PREDICT, edible_percent=0)).get_json()
    assert r["edible_kg"] == 0 and r["donate_kg"] == 0 and r["decision"] == "DISPOSE"


def test_expired_food_is_never_donated_whatever_the_share(client):
    r = client.post("/predict", json=dict(PREDICT, hours_to_expiry=1)).get_json()
    assert r["decision"] == "DO_NOT_DONATE" and r["donate_kg"] == 0


def test_out_of_range_input_returns_a_warning_not_a_crash(client):
    r = client.post("/predict", json=dict(PREDICT, meals_served=5000, food_prepared_kg=1750))
    assert r.status_code == 200 and any("outside the range" in w for w in r.get_json()["input_warnings"])


@pytest.mark.parametrize("override,fragment", [
    ({"edible_percent": 150}, "Edible share"), ({"edible_percent": "abc"}, "Edible share"),
    ({"meals_served": 10, "food_prepared_kg": 9000}, "per meal"),
    ({"meals_served": 50000, "food_prepared_kg": 1}, "per meal"),
])
def test_bad_new_inputs_are_rejected(client, override, fragment):
    r = client.post("/predict", json=dict(PREDICT, **override))
    assert r.status_code == 400 and fragment in r.get_json()["error"]


def test_prediction_is_saved_for_the_overview(client, appmod):
    client.post("/predict", json=PREDICT)
    assert appmod.gather_stats()["predictions"] == 1
