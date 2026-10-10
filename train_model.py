"""Trains and compares models, then saves the best one. Run manually: python train_model.py

Steps: load + validate data -> handle missing values -> encode day_of_week / special_event
-> train/test split -> train Linear Regression and Random Forest -> MAE and R2 -> save best.
"""
import sys
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import config
from generate_data import generate


def load_dataset(path):
    """Loads the CSV, validates columns and cleans it. Generates synthetic data if missing."""
    path = Path(path)
    if not path.exists():
        print(f"Dataset not found at {path}. Generating a synthetic dataset...")
        path.parent.mkdir(parents=True, exist_ok=True)
        generate().to_csv(path, index=False)

    df = pd.read_csv(path)
    needed = config.FEATURES + [config.TARGET]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")

    df = df[needed].copy()
    for col in needed:
        if col != "special_event":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Encode special_event as 0/1 (accepts yes/no/true/false/0/1); unknown values become 0.
    df["special_event"] = df["special_event"].map(config.normalize_event).fillna(0).astype(int)

    # Rows without a target are useless; negative waste is impossible.
    df = df.dropna(subset=[config.TARGET])
    df = df[df[config.TARGET] >= 0].drop_duplicates()

    # day_of_week must be an integer 0-6; fix missing/invalid values with the most common day.
    day = df["day_of_week"].round()
    day = day.where(day.between(0, 6))
    df["day_of_week"] = day.fillna(day.mode().iloc[0] if not day.mode().empty else 0).astype(int)

    # Remaining numeric gaps are filled by the median inside the pipeline (SimpleImputer).
    if len(df) < 50:
        raise ValueError(f"Not enough valid rows to train ({len(df)}).")
    return df


def build_pipeline(regressor):
    """Preprocessing + model in ONE object, so inference uses exactly the training preprocessing."""
    pre = ColumnTransformer([
        # day_of_week is categorical (Mon..Sun), so one-hot encode it
        ("day", OneHotEncoder(categories=[list(range(7))], handle_unknown="ignore",
                              sparse_output=False), ["day_of_week"]),
        # numeric columns: fill gaps with median, then scale
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                          ("scale", StandardScaler())]), config.NUMERIC_FEATURES),
        # special_event is already 0/1
        ("event", "passthrough", ["special_event"]),
    ])
    return Pipeline([("pre", pre), ("model", regressor)])


def aggregate_importances(pipeline):
    """Random Forest importances, with the 7 one-hot day columns merged back into day_of_week."""
    names = pipeline.named_steps["pre"].get_feature_names_out()
    values = pipeline.named_steps["model"].feature_importances_
    out = {}
    for name, value in zip(names, values):
        key = "day_of_week" if "day_of_week" in name else name.split("__", 1)[1]
        out[key] = out.get(key, 0.0) + float(value)
    return {k: round(v, 4) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def train(data_path=None, model_path=None):
    data_path = data_path or config.DATA_PATH
    model_path = Path(model_path or config.MODEL_PATH)

    df = load_dataset(data_path)
    X, y = df[config.FEATURES], df[config.TARGET]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    candidates = {
        "Linear Regression": build_pipeline(LinearRegression()),
        "Random Forest": build_pipeline(
            RandomForestRegressor(n_estimators=100, min_samples_leaf=3, random_state=42, n_jobs=-1)),
    }

    metrics = {}
    print(f"Training on {len(X_train)} rows, testing on {len(X_test)} rows\n")
    print(f"{'Model':<20}{'MAE (kg)':>10}{'R2':>10}")
    for name, pipe in candidates.items():
        pipe.fit(X_train, y_train)
        pred = pipe.predict(X_test)
        metrics[name] = {"MAE": round(float(mean_absolute_error(y_test, pred)), 3),
                         "R2": round(float(r2_score(y_test, pred)), 3)}
        print(f"{name:<20}{metrics[name]['MAE']:>10}{metrics[name]['R2']:>10}")

    # Best = lowest MAE (average error in kg is the most interpretable metric here)
    best_name = min(metrics, key=lambda k: metrics[k]["MAE"])
    print(f"\nBest model: {best_name}")

    bundle = {
        "pipeline": candidates[best_name],
        "model_name": best_name,
        "features": config.FEATURES,
        "metrics": metrics,
        "importances": aggregate_importances(candidates["Random Forest"]),
        "weekday_avg": [round(float(v), 2) for v in
                        df.groupby("day_of_week")[config.TARGET].mean().reindex(range(7)).fillna(0)],
        "n_rows": int(len(df)),
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path)
    print(f"Saved model to {model_path}")
    return bundle


if __name__ == "__main__":
    try:
        train()
    except (ValueError, OSError) as exc:
        print(f"Training failed: {exc}", file=sys.stderr)
        sys.exit(1)
