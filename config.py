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
RAW_NUMERIC = ["meals_served", "food_prepared_kg", "kitchen_staff",
               "temperature", "humidity", "previous_waste_kg"]
# prepared_per_meal is derived inside the model pipeline (features.py)
NUMERIC_FEATURES = RAW_NUMERIC + ["prepared_per_meal"]
FEATURES = ["meals_served", "food_prepared_kg", "kitchen_staff", "day_of_week",
            "temperature", "humidity", "special_event", "previous_waste_kg"]
TARGET = "waste_kg"

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FOOD_TYPES = ["Cooked rice", "Cooked vegetables", "Dal / curry", "Bread / chapati",
              "Packed food", "Fruits", "Sweets / desserts"]

# Donation lifecycle. Every transition is stored as a NEW block on the chain and as a row in
# donation_events. Terminal states have no outgoing transitions.
STATUS_TRANSITIONS = {
    "Pending": ["Accepted", "Rejected", "Cancelled"],
    "Accepted": ["Picked Up", "Cancelled"],
    "Picked Up": ["In Transit", "Failed"],
    "In Transit": ["Delivered", "Failed"],
    "Delivered": [], "Cancelled": [], "Rejected": [], "Failed": [],
}
HAPPY_PATH = ["Pending", "Accepted", "Picked Up", "In Transit", "Delivered"]
EXCEPTIONAL_STATUSES = ["Cancelled", "Rejected", "Failed"]
TERMINAL_STATUSES = [s for s, nxt in STATUS_TRANSITIONS.items() if not nxt]
ALL_STATUSES = HAPPY_PATH + EXCEPTIONAL_STATUSES
# Next step on the normal path (used for the primary action button).
STATUS_FLOW = {a: b for a, b in zip(HAPPY_PATH, HAPPY_PATH[1:])}

# label = short name, text = plain-language explanation, actor = who normally does this step
STATUS_INFO = {
    "Pending": {"label": "Registered", "actor": "Donor",
                "text": "The donation was registered and is waiting for the NGO to accept it."},
    "Accepted": {"label": "Accepted by NGO", "actor": "Receiving NGO",
                 "text": "The NGO confirmed it can take this food and will arrange pickup."},
    "Picked Up": {"label": "Picked up", "actor": "Receiving NGO",
                  "text": "The food was collected from the donor."},
    "In Transit": {"label": "In transit", "actor": "Receiving NGO",
                   "text": "The food is on its way to the NGO."},
    "Delivered": {"label": "Delivered", "actor": "Receiving NGO",
                  "text": "The food reached the NGO. This donation is complete."},
    "Cancelled": {"label": "Cancelled", "actor": "Donor or NGO",
                  "text": "The donation was cancelled before pickup."},
    "Rejected": {"label": "Rejected by NGO", "actor": "Receiving NGO",
                 "text": "The NGO could not accept this donation."},
    "Failed": {"label": "Delivery failed", "actor": "Receiving NGO",
               "text": "The food could not be delivered. It needs follow-up."},
}

# Where the server listens, and the public address printed in QR codes.
BIND_HOST = os.environ.get("HOST", "127.0.0.1")
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
# Operator actions (create donation, change status, demo tools) are limited to this computer
# unless this is set to 1.
ALLOW_REMOTE_OPERATOR = os.environ.get("FOOD_ALLOW_REMOTE_OPERATOR") == "1"


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
