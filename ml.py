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
    return clean


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
