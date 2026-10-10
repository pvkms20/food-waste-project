"""Waste-risk classification and the donate / dispose decision engine.

All thresholds live here so they can be tuned in one place.
DEMO ONLY: these rules are a simplified academic model, not a real food-safety assessment.
"""

# Risk bands for predicted waste (kg): < LOW_MAX = LOW, LOW_MAX..MEDIUM_MAX = MEDIUM, above = HIGH
RISK_LOW_MAX = 5.0
RISK_MEDIUM_MAX = 15.0

# Donation rules (deliberately conservative)
MIN_DONATION_KG = 2.0    # below this a pickup is not worthwhile
MIN_SAFE_HOURS = 3.0     # food must stay safe at least this long to be collected and served


def classify_risk(waste_kg):
    """Returns 'LOW', 'MEDIUM' or 'HIGH' for a predicted waste quantity in kg."""
    if waste_kg < RISK_LOW_MAX:
        return "LOW"
    if waste_kg <= RISK_MEDIUM_MAX:
        return "MEDIUM"
    return "HIGH"


def decide(quantity_kg, hours_to_expiry, ngo_capacity_kg=None, risk=None):
    """Decides what to do with surplus food.

    Returns a dict:
      decision   DONATE | DISPOSE | DO_NOT_DONATE
      reason     human-readable explanation
      risk       LOW | MEDIUM | HIGH (waste risk of the quantity)
      warning    extra caution text, or None
      donate_kg  how much to donate (0 unless DONATE)
    """
    risk = risk or classify_risk(quantity_kg)

    def result(decision, reason, warning=None, donate_kg=0.0):
        return {"decision": decision, "reason": reason, "risk": risk,
                "warning": warning, "donate_kg": round(donate_kg, 1)}

    # 1. Safety first: expired or nearly expired food is never donated.
    if hours_to_expiry <= 0:
        return result("DO_NOT_DONATE", "The food has already expired and must not be donated.")
    if hours_to_expiry < MIN_SAFE_HOURS:
        return result("DO_NOT_DONATE",
                      f"Only {hours_to_expiry:g} h of safe shelf life remain, below the "
                      f"{MIN_SAFE_HOURS:g} h needed for pickup and serving.")

    # 2. Not worth a pickup.
    if quantity_kg < MIN_DONATION_KG:
        return result("DISPOSE",
                      f"{quantity_kg:.1f} kg is below the minimum donation size of "
                      f"{MIN_DONATION_KG:g} kg. Prepare less next time.")

    # 3. Donate, limited by what the NGO can take.
    donate_kg = quantity_kg if ngo_capacity_kg is None else min(quantity_kg, ngo_capacity_kg)
    reason = f"{quantity_kg:.1f} kg surplus with {hours_to_expiry:g} h before expiry is suitable for donation."
    if donate_kg < quantity_kg:
        reason += f" The matched NGO can take {donate_kg:.1f} kg; the rest needs another NGO."

    warning = None
    if risk == "HIGH":
        warning = ("High waste risk: check storage temperature and food quality before handing over, "
                   "and reduce preparation next time.")
    return result("DONATE", reason, warning, donate_kg)
