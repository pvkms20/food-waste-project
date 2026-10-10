"""Derived model features. Used inside the sklearn Pipeline so training and prediction match."""
import numpy as np
import pandas as pd


def add_features(frame):
    """Adds prepared_per_meal (kg of food prepared per meal served). Missing/zero meals give NaN,
    which the pipeline's median imputer fills."""
    frame = pd.DataFrame(frame).copy()
    meals = pd.to_numeric(frame["meals_served"], errors="coerce").replace(0, np.nan)
    frame["prepared_per_meal"] = pd.to_numeric(frame["food_prepared_kg"], errors="coerce") / meals
    return frame
