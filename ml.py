"""Inference helpers: load the saved model, validate user input, predict waste."""
import logging
import math
import os

import joblib
import pandas as pd

import config

log = logging.getLogger(__name__)


class InputError(ValueError):
    """Raised when user input is missing or invalid (message is safe to show to the user)."""


MIN_KG_PER_MEAL, MAX_KG_PER_MEAL = 0.02, 5.0

# name: (min, max, label shown in error messages)
INPUT_RULES = {
    "meals_served": (1, 100000, "Meals served"),
    "food_prepared_kg": (0.1, 100000, "Food prepared (kg)"),
    "kitchen_staff": (1, 1000, "Kitchen staff"),
    "temperature": (-30, 60, "Temperature"),
    "humidity": (0, 100, "Humidity"),
    "previous_waste_kg": (0, 100000, "Previous waste (kg)"),
}


def validate_inputs(raw):
    """Returns a clean dict of model inputs or raises InputError."""
    if not isinstance(raw, dict):
        raise InputError("No input received.")
    clean = {}
    for name, (low, high, label) in INPUT_RULES.items():
        value = raw.get(name)
        if value is None or str(value).strip() == "":
            raise InputError(f"{label} is required.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise InputError(f"{label} must be a number.")
        if not math.isfinite(number) or number < low or number > high:
            raise InputError(f"{label} must be between {low:g} and {high:g}.")
        clean[name] = number

    try:
        day = float(raw.get("day_of_week"))
    except (TypeError, ValueError):
        raise InputError("Day of week must be selected.")
    if day != int(day) or not 0 <= day <= 6:
        raise InputError("Day of week must be between 0 (Monday) and 6 (Sunday).")
    clean["day_of_week"] = int(day)

    event = config.normalize_event(raw.get("special_event", 0))
    if event is None:
        raise InputError("Special event must be Yes or No.")
    clean["special_event"] = event

    per_meal = clean["food_prepared_kg"] / clean["meals_served"]
    if not MIN_KG_PER_MEAL <= per_meal <= MAX_KG_PER_MEAL:
        raise InputError(
            f"Food prepared ({clean['food_prepared_kg']:g} kg) for {clean['meals_served']:g} meals is "
            f"{per_meal:.3f} kg per meal. That is not plausible (expected {MIN_KG_PER_MEAL:g} to "
            f"{MAX_KG_PER_MEAL:g} kg per meal). Please check both numbers.")
    return clean


def parse_edible_percent(raw):
    """Share (0-100) of the predicted waste the user judges still safe to donate. Default 100."""
    value = raw.get("edible_percent")
    if value is None or str(value).strip() == "":
        return 100.0
    try:
        pct = float(value)
    except (TypeError, ValueError):
        raise InputError("Edible share must be a number between 0 and 100.")
    if not math.isfinite(pct) or not 0 <= pct <= 100:
        raise InputError("Edible share must be between 0 and 100.")
    return pct


LABELS = {name: label for name, (_, _, label) in INPUT_RULES.items()}
LABELS["prepared_per_meal"] = "Food prepared per meal"


def input_warnings(bundle, inputs):
    """Plain-language warnings when inputs fall outside the range the model learned from.
    Tree models cannot extrapolate, so such predictions are unreliable."""
    out = []
    ranges = (bundle or {}).get("ranges") or {}
    values = dict(inputs)
    values["prepared_per_meal"] = inputs["food_prepared_kg"] / inputs["meals_served"]
    for name, r in ranges.items():
        v = values.get(name)
        if v is None:
            continue
        if v < r["min"] or v > r["max"]:
            out.append(f"{LABELS.get(name, name)} ({v:g}) is outside the range the model was trained on "
                       f"({r['min']:g} to {r['max']:g}). Treat this prediction as unreliable.")
    return out


def load_bundle(path=None):
    """Loads the trained model bundle. Returns None (and logs) if it is missing or unreadable."""
    path = path or config.MODEL_PATH
    if not os.path.exists(path):
        log.warning("Model file not found at %s. Run: python train_model.py", path)
        return None
    try:
        bundle = joblib.load(path)
        for key in ("pipeline", "features", "metrics", "model_name"):
            if key not in bundle:
                raise KeyError(key)
        return bundle
    except Exception:
        log.exception("Could not load model file %s", path)
        return None


def predict_waste(bundle, inputs):
    """Predicted waste in kg. Never negative and never more than the food prepared."""
    frame = pd.DataFrame([inputs])[bundle["features"]]
    value = float(bundle["pipeline"].predict(frame)[0])
    if not math.isfinite(value):
        raise ValueError("Model returned a non-numeric prediction.")
    return round(min(max(value, 0.0), inputs["food_prepared_kg"]), 1)


def predict_interval(bundle, value, food_prepared_kg):
    """Empirical 90% interval (from out-of-fold residuals), clipped to the physical range."""
    iv = bundle.get("interval")
    if not iv:
        return None
    low = max(0.0, value + iv["low_offset"])
    high = min(food_prepared_kg, value + iv["high_offset"])
    return round(min(low, value), 1), round(max(high, value), 1)
