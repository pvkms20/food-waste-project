"""Generates a synthetic food waste dataset (data/waste_data.csv).

The relationships are plausible but noisy and non-linear, so waste cannot be computed
from a single column. You can replace the CSV with a real dataset that has the same columns.
"""
from pathlib import Path

import numpy as np
import pandas as pd

import config


def generate(n=2000, seed=42):
    rng = np.random.default_rng(seed)

    meals = rng.integers(50, 501, n)
    day = rng.integers(0, 7, n)  # 0 = Monday ... 6 = Sunday
    event = (rng.random(n) < 0.12).astype(int)
    staff = np.clip((meals / 40 + rng.normal(0, 2, n)).round(), 3, 25).astype(int)
    temp = rng.normal(28, 6, n).round(1)
    humidity = np.clip(rng.normal(60, 15, n), 20, 95).round(1)

    # Kitchens over-prepare by 0-40% (a bit more on event days)
    factor = rng.uniform(1.0, 1.4, n) + 0.1 * event
    prepared = (meals * 0.35 * factor).round(1)
    over = prepared - meals * 0.35
    prev_waste = np.clip(0.15 * over + rng.gamma(2.0, 2.5, n), 0, None).round(1)  # mostly its own noise

    shortage = np.clip(meals / 40 - staff, 0, None)  # understaffed kitchens waste more
    multiplier = 1 + 0.35 * event + 0.20 * (day >= 5)  # events and weekends amplify waste
    waste = 0.6 * (
        (0.35 * over + 0.30 * prev_waste) * multiplier
        + 0.012 * over ** 2                                   # large over-preparation is disproportionately wasted
        + 0.10 * np.clip(temp - 30, 0, None) ** 2             # heat spoils food
        + 0.015 * np.clip(temp - 28, 0, None) * np.clip(humidity - 65, 0, None)  # hot AND humid
        + 0.6 * shortage ** 1.4
        + 2.5 * event * (meals > 300)
        + 3.0 * ((day == 4) | (day == 5)) * (meals > 350)     # big Friday/Saturday crowds
    ) + rng.normal(0, 1.5, n)
    waste = np.clip(waste, 0, None).round(1)

    return pd.DataFrame({
        "meals_served": meals,
        "food_prepared_kg": prepared,
        "kitchen_staff": staff,
        "day_of_week": day,
        "temperature": temp,
        "humidity": humidity,
        "special_event": event,
        "previous_waste_kg": prev_waste,
        "waste_kg": waste,
    })


def main(path=None):
    path = Path(path or config.DATA_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = generate()
    df.to_csv(path, index=False)
    print(f"Saved {path} with {len(df)} rows")
    return df


if __name__ == "__main__":
    main()
