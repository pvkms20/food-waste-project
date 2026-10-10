"""Trains and compares models, then saves the best one. Run manually: python train_model.py

Method (so the reported numbers are honest):
1. 20% of the rows are held out as a TEST set and never used for any choice.
2. On the remaining 80% each candidate is scored with 5-fold cross-validation (MAE); the model
   with the lowest CV MAE is selected. HistGradientBoosting is tuned with a small grid search
   inside that training data only.
3. The test set is used once, to report MAE / RMSE / R2 for every model and two simple baselines.
4. Out-of-fold residuals on the training data give an empirical 90% prediction interval, whose
   real coverage is then checked on the test set.
The default dataset is SYNTHETIC (generate_data.py), so these numbers describe how well the model
learns that synthetic pattern, not real-world kitchen accuracy.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GridSearchCV, KFold, cross_val_predict, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

import config
from features import add_features
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
    """Feature engineering + preprocessing + model in ONE object, so inference uses exactly the
    training preprocessing."""
    pre = ColumnTransformer([
        # day_of_week is categorical (Mon..Sun), so one-hot encode it
        ("day", OneHotEncoder(categories=[list(range(7))], handle_unknown="ignore",
                              sparse_output=False), ["day_of_week"]),
        # numeric columns (incl. derived prepared_per_meal): fill gaps with median, then scale
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                          ("scale", StandardScaler())]), config.NUMERIC_FEATURES),
        # special_event is already 0/1
        ("event", "passthrough", ["special_event"]),
    ])
    return Pipeline([("features", FunctionTransformer(add_features)), ("pre", pre), ("model", regressor)])


def score(y_true, pred):
    return {"MAE": round(float(mean_absolute_error(y_true, pred)), 3),
            "RMSE": round(float(np.sqrt(mean_squared_error(y_true, pred))), 3),
            "R2": round(float(r2_score(y_true, pred)), 3)}


def feature_ranges(df):
    out = {}
    for col in config.RAW_NUMERIC:
        out[col] = {"min": round(float(df[col].min()), 2), "max": round(float(df[col].max()), 2),
                    "p1": round(float(df[col].quantile(.01)), 2), "p99": round(float(df[col].quantile(.99)), 2)}
    ratio = (df["food_prepared_kg"] / df["meals_served"].replace(0, np.nan)).dropna()
    out["prepared_per_meal"] = {"min": round(float(ratio.min()), 3), "max": round(float(ratio.max()), 3),
                                "p1": round(float(ratio.quantile(.01)), 3), "p99": round(float(ratio.quantile(.99)), 3)}
    return out


def train(data_path=None, model_path=None):
    data_path = data_path or config.DATA_PATH
    model_path = Path(model_path or config.MODEL_PATH)

    df = load_dataset(data_path)
    X, y = df[config.FEATURES], df[config.TARGET]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    cv = KFold(n_splits=5, shuffle=True, random_state=42)

    hgb_grid = GridSearchCV(
        build_pipeline(HistGradientBoostingRegressor(random_state=42)),
        {"model__learning_rate": [0.05, 0.1], "model__max_depth": [3, None],
         "model__min_samples_leaf": [10, 20], "model__max_iter": [200, 400]},
        scoring="neg_mean_absolute_error", cv=cv, n_jobs=-1, refit=True)
    candidates = {
        "Linear Regression": build_pipeline(LinearRegression()),
        "Random Forest": build_pipeline(
            RandomForestRegressor(n_estimators=200, min_samples_leaf=3, random_state=42, n_jobs=-1)),
        "Gradient Boosting": hgb_grid,
    }

    print(f"Training rows: {len(X_train)}   held-out test rows: {len(X_test)}\n")
    cv_mae = {}
    for name, est in candidates.items():
        if name == "Gradient Boosting":
            est.fit(X_train, y_train)
            cv_mae[name] = round(float(-est.best_score_), 3)
            candidates[name] = est.best_estimator_
            print(f"  tuned Gradient Boosting: {est.best_params_}")
        else:
            cv_mae[name] = round(float(-cross_val_score(est, X_train, y_train, cv=cv,
                                                        scoring="neg_mean_absolute_error").mean()), 3)
            est.fit(X_train, y_train)

    # Selection uses cross-validation on the training data only (never the test set).
    best_name = min(cv_mae, key=cv_mae.get)

    metrics = {name: score(y_test, pipe.predict(X_test)) for name, pipe in candidates.items()}
    for name in metrics:
        metrics[name]["CV_MAE"] = cv_mae[name]
    baselines = {
        "Predict the training mean": score(y_test, np.full(len(y_test), y_train.mean())),
        "Predict previous waste": score(y_test, X_test["previous_waste_kg"]),
    }

    print(f"{'Model':<22}{'CV MAE':>9}{'Test MAE':>10}{'RMSE':>8}{'R2':>8}")
    for name, m in metrics.items():
        print(f"{name:<22}{m['CV_MAE']:>9}{m['MAE']:>10}{m['RMSE']:>8}{m['R2']:>8}")
    for name, m in baselines.items():
        print(f"{name:<22}{'':>9}{m['MAE']:>10}{m['RMSE']:>8}{m['R2']:>8}")
    print(f"\nSelected by CV MAE: {best_name}")

    # Empirical 90% interval from out-of-fold residuals (training data only).
    best = candidates[best_name]
    oof = cross_val_predict(build_pipeline(best.named_steps["model"].__class__(**best.named_steps["model"].get_params())),
                            X_train, y_train, cv=cv)
    resid = y_train.to_numpy() - oof
    lo, hi = float(np.quantile(resid, 0.05)), float(np.quantile(resid, 0.95))
    test_pred = best.predict(X_test)
    coverage = float(np.mean((y_test.to_numpy() >= test_pred + lo) & (y_test.to_numpy() <= test_pred + hi)))
    print(f"90% interval offsets: {lo:+.2f} / {hi:+.2f} kg, observed test coverage {coverage:.1%}")

    perm = permutation_importance(best, X_test, y_test, n_repeats=10, random_state=42,
                                  scoring="neg_mean_absolute_error", n_jobs=-1)
    importances = {c: round(float(v), 4) for c, v in
                   sorted(zip(config.FEATURES, perm.importances_mean), key=lambda kv: -kv[1])}

    bundle = {
        "pipeline": best,
        "model_name": best_name,
        "selection": "lowest 5-fold cross-validated MAE on the training split",
        "features": config.FEATURES,
        "metrics": metrics,
        "baselines": baselines,
        "interval": {"level": 0.90, "low_offset": round(lo, 3), "high_offset": round(hi, 3),
                     "test_coverage": round(coverage, 3)},
        "importances": importances,
        "importance_note": "Increase in test MAE when the feature is shuffled (permutation importance)",
        "ranges": feature_ranges(df),
        "data_source": "synthetic (generate_data.py)" if Path(data_path) == Path(config.DATA_PATH)
                       and "FOOD_DATA_PATH" not in os.environ else "user-supplied dataset",
        "n_rows": int(len(df)), "n_train": int(len(X_train)), "n_test": int(len(X_test)),
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
