import os

import joblib
import numpy as np
import pandas as pd
import pytest

import config
import ml
import train_model
from generate_data import generate

VALID = dict(meals_served=300, food_prepared_kg=125, kitchen_staff=8, day_of_week=2,
             temperature=30, humidity=60, special_event=0, previous_waste_kg=10)


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """Trains a fresh model on a small dataset in a temp folder (does not touch model/model.pkl)."""
    folder = tmp_path_factory.mktemp("ml")
    csv, model = folder / "data.csv", folder / "model.pkl"
    generate(n=500, seed=1).to_csv(csv, index=False)
    bundle = train_model.train(csv, model)
    return csv, model, bundle


def test_dataset_loads_with_required_columns():
    df = train_model.load_dataset(config.DATA_PATH)
    assert set(config.FEATURES + [config.TARGET]) <= set(df.columns)
    assert len(df) >= 500 and df[config.TARGET].min() >= 0


def test_dataset_is_not_trivially_one_column():
    df = train_model.load_dataset(config.DATA_PATH)
    assert df[config.TARGET].corr(df["meals_served"]) < 0.99
    assert df[config.TARGET].corr(df["food_prepared_kg"]) < 0.99


def test_load_dataset_rejects_missing_columns(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"meals_served": [1, 2, 3]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="missing required columns"):
        train_model.load_dataset(path)


def test_load_dataset_generates_when_missing(tmp_path):
    df = train_model.load_dataset(tmp_path / "new" / "waste.csv")
    assert len(df) > 100 and (tmp_path / "new" / "waste.csv").exists()


def test_missing_values_and_event_text_are_handled(tmp_path):
    df = generate(n=300, seed=3)
    df.loc[:20, "temperature"] = np.nan
    df.loc[:10, "day_of_week"] = np.nan
    df["special_event"] = df["special_event"].map({0: "No", 1: "Yes"})
    df.loc[0, "waste_kg"] = np.nan
    path = tmp_path / "messy.csv"
    df.to_csv(path, index=False)
    clean = train_model.load_dataset(path)
    assert clean["day_of_week"].between(0, 6).all()
    assert set(clean["special_event"].unique()) <= {0, 1}
    train_model.train(path, tmp_path / "m.pkl")  # NaN temperatures must not crash training


def test_training_evaluates_two_models_and_saves_best(trained):
    _, model_path, bundle = trained
    assert os.path.exists(model_path)
    assert set(bundle["metrics"]) == {"Linear Regression", "Random Forest"}
    for m in bundle["metrics"].values():
        assert m["MAE"] > 0 and -1 <= m["R2"] <= 1
    best = min(bundle["metrics"], key=lambda k: bundle["metrics"][k]["MAE"])
    assert bundle["model_name"] == best


def test_saved_model_loads_and_predicts_a_number(trained):
    _, model_path, _ = trained
    bundle = ml.load_bundle(model_path)
    value = ml.predict_waste(bundle, ml.validate_inputs(VALID))
    assert isinstance(value, float) and 0 <= value <= VALID["food_prepared_kg"]


def test_pipeline_includes_preprocessing(trained):
    _, model_path, _ = trained
    pipe = joblib.load(model_path)["pipeline"]
    assert list(pipe.named_steps) == ["pre", "model"]
    # day_of_week is one-hot encoded: 7 day columns + 6 numeric + 1 event
    assert len(pipe.named_steps["pre"].get_feature_names_out()) == 14


def test_importances_cover_all_features(trained):
    assert set(trained[2]["importances"]) == set(config.FEATURES)


def test_real_model_file_exists_and_is_good():
    bundle = ml.load_bundle(config.MODEL_PATH)
    assert bundle is not None
    assert max(m["R2"] for m in bundle["metrics"].values()) > 0.8


def test_more_overproduction_predicts_more_waste():
    bundle = ml.load_bundle(config.MODEL_PATH)
    lo = ml.predict_waste(bundle, ml.validate_inputs({**VALID, "food_prepared_kg": 106}))
    hi = ml.predict_waste(bundle, ml.validate_inputs({**VALID, "food_prepared_kg": 150}))
    assert hi > lo


def test_load_bundle_missing_or_corrupt_returns_none(tmp_path):
    assert ml.load_bundle(tmp_path / "nope.pkl") is None
    bad = tmp_path / "bad.pkl"
    bad.write_bytes(b"not a model")
    assert ml.load_bundle(bad) is None


@pytest.mark.parametrize("change", [
    {"meals_served": ""}, {"meals_served": "abc"}, {"meals_served": -5},
    {"humidity": 150}, {"temperature": "nan"}, {"day_of_week": 9}, {"day_of_week": "x"},
    {"special_event": "maybe"}, {"previous_waste_kg": -1}, {"food_prepared_kg": 0},
])
def test_invalid_inputs_are_rejected(change):
    with pytest.raises(ml.InputError):
        ml.validate_inputs({**VALID, **change})


def test_missing_field_is_rejected():
    data = dict(VALID)
    del data["humidity"]
    with pytest.raises(ml.InputError, match="Humidity"):
        ml.validate_inputs(data)


def test_special_event_accepts_text():
    assert ml.validate_inputs({**VALID, "special_event": "Yes"})["special_event"] == 1
    assert ml.validate_inputs({**VALID, "special_event": "no"})["special_event"] == 0
