"""Executable checklist of the project specification (structure, schema, routes, safety, docs)."""
import os
import re
import sqlite3

import joblib
import pandas as pd
import pytest

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_SOURCES = ["app.py", "blockchain.py", "ml.py", "train_model.py", "decision.py", "config.py"]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


# ---------------- structure ----------------
@pytest.mark.parametrize("path", [
    "app.py", "train_model.py", "blockchain.py", "decision.py", "requirements.txt", "README.md",
    "data/waste_data.csv", "model/model.pkl",
    "templates/index.html", "templates/donate.html", "templates/chain.html",
    "static/css/style.css", "static/js/app.js",
    "tests/test_blockchain.py", "tests/test_decision.py", "tests/test_app.py",
])
def test_required_files_exist(path):
    assert os.path.isfile(os.path.join(ROOT, path)), path


def test_requirements_list_all_packages():
    text = read("requirements.txt").lower()
    for pkg in ("flask", "pandas", "numpy", "scikit-learn", "joblib", "qrcode", "pytest"):
        assert pkg in text, pkg


def test_readme_has_every_required_section():
    headings = " ".join(re.findall(r"^#+\s*(.+)$", read("README.md"), re.M)).lower()
    for section in ("project overview", "architecture", "features", "installation", "dataset", "model training",
                    "running the application", "testing", "routes", "ml methodology", "blockchain methodology",
                    "limitations"):
        assert section in headings, section
    text = read("README.md")
    assert "python -m venv venv" in text and "pip install -r requirements.txt" in text
    assert "python train_model.py" in text and "python app.py" in text and "pytest" in text


# ---------------- dataset and ML ----------------
def test_dataset_has_exactly_the_required_columns():
    df = pd.read_csv(config.DATA_PATH)
    assert list(df.columns) == ["meals_served", "food_prepared_kg", "kitchen_staff", "day_of_week", "temperature",
                                "humidity", "special_event", "previous_waste_kg", "waste_kg"]
    assert len(df) >= 1000


def test_saved_model_compares_both_models_and_keeps_the_best():
    bundle = joblib.load(config.MODEL_PATH)
    assert set(bundle["metrics"]) == {"Linear Regression", "Random Forest"}
    for m in bundle["metrics"].values():
        assert {"MAE", "R2"} <= set(m)
    assert bundle["model_name"] == min(bundle["metrics"], key=lambda k: bundle["metrics"][k]["MAE"])
    assert type(bundle["pipeline"]).__name__ == "Pipeline"


def test_app_never_trains_at_startup():
    source = read("app.py")
    # The app may TELL the user to run train_model.py, but must never import or call training code.
    assert not re.search(r"^\s*(import|from)\s+(train_model|generate_data)\b", source, re.M)
    assert ".fit(" not in source and "train(" not in source
    assert "joblib.load" in read("ml.py")


def test_risk_thresholds_live_in_decision_module():
    import decision
    assert (decision.RISK_LOW_MAX, decision.RISK_MEDIUM_MAX) == (5.0, 15.0)
    assert not re.search(r"\b(5|15)(\.0)?\s*(kg)?\s*[<>]", read("app.py"))  # no magic numbers in the app


# ---------------- blockchain API ----------------
def test_blockchain_public_api():
    import blockchain
    block = blockchain.Block(1, "t", {}, "0")
    assert {"index", "timestamp", "data", "previous_hash", "hash"} <= set(block.to_dict())
    assert callable(blockchain.Blockchain.add_block) and callable(blockchain.Blockchain.is_valid)
    assert len(block.hash) == 64  # SHA-256 hex digest


# ---------------- database schema ----------------
def columns(table):
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_database_schema_matches_spec(appmod):
    assert {"id", "donor", "food_type", "quantity", "expiry", "ngo", "status"} <= columns("donations")
    assert {"id", "name", "location", "capacity"} <= columns("ngos")
    assert {"index", "timestamp", "data", "prev_hash", "hash"} <= columns("blocks")
    assert len(appmod.get_ngos()) >= 3


# ---------------- routes ----------------
def test_all_required_routes_are_registered(appmod):
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    for route in ("/", "/predict", "/donate", "/chain", "/verify", "/tamper", "/status"):
        assert route in rules, route


def test_status_returns_json_with_required_fields(client):
    resp = client.get("/status")
    assert resp.mimetype == "application/json"
    assert {"model_available", "database_available", "donations", "blockchain_blocks",
            "blockchain_valid"} <= set(resp.get_json())


# ---------------- security / quality ----------------
@pytest.mark.parametrize("name", PY_SOURCES)
def test_no_eval_exec_or_string_built_sql(name):
    source = read(name)
    assert not re.search(r"\b(eval|exec)\(", source)
    assert not re.search(r"execute\(\s*f[\"']", source)            # f-string SQL
    assert not re.search(r"execute\([^)\n]*\.format\(", source)     # .format SQL
    assert not re.search(r"execute\([^)\n]*[\"']\s*%\s*[\w(]", source)  # % SQL


def test_no_hardcoded_secret_key():
    for name in ("app.py", "config.py"):
        assert not re.search(r"SECRET_KEY[\"']?\]?\s*=\s*[\"'][^\"']+[\"']", read(name)), name


def test_debug_mode_is_off_by_default():
    source = read("app.py")
    assert 'debug=os.environ.get("FLASK_DEBUG") == "1"' in source
    assert "debug=True" not in source


def test_templates_do_not_disable_escaping():
    for name in os.listdir(os.path.join(ROOT, "templates")):
        text = read(os.path.join("templates", name))
        assert "|safe" not in text and "autoescape false" not in text, name
    assert ".innerHTML" not in read("static/js/app.js")


def test_all_pages_are_responsive():
    assert 'name="viewport"' in read("templates/base.html")
