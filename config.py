"""Central configuration: paths, feature names, constants shared across modules."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Paths can be overridden with environment variables (the tests use a throwaway database).
DB_PATH = os.environ.get("FOOD_DB_PATH", str(BASE_DIR / "database.db"))
MODEL_PATH = os.environ.get("FOOD_MODEL_PATH", str(BASE_DIR / "model" / "model.pkl"))
DATA_PATH = os.environ.get("FOOD_DATA_PATH", str(BASE_DIR / "data" / "waste_data.csv"))

# No hardcoded secret: use SECRET_KEY from the environment, otherwise a random key per run.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(24)

# ML columns
NUMERIC_FEATURES = ["meals_served", "food_prepared_kg", "kitchen_staff",
                    "temperature", "humidity", "previous_waste_kg"]
FEATURES = ["meals_served", "food_prepared_kg", "kitchen_staff", "day_of_week",
            "temperature", "humidity", "special_event", "previous_waste_kg"]
TARGET = "waste_kg"

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FOOD_TYPES = ["Cooked rice", "Cooked vegetables", "Dal / curry", "Bread / chapati",
              "Packed food", "Fruits", "Sweets / desserts"]

# Donation lifecycle: each transition is stored as a NEW block on the chain.
STATUS_FLOW = {"Pending": "Picked Up", "Picked Up": "Delivered"}


def normalize_event(value):
    """Encodes the special_event column as 0/1. Returns None if the value is not recognised."""
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    if text in {"1", "1.0", "yes", "y", "true", "t"}:
        return 1
    if text in {"0", "0.0", "no", "n", "false", "f"}:
        return 0
    return None
