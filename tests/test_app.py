import json
import re
import sqlite3
from datetime import datetime, timedelta

import pytest

import config

PREDICT = dict(meals_served=300, food_prepared_kg=140, kitchen_staff=8, day_of_week=5,
               temperature=33, humidity=70, special_event=1, previous_waste_kg=12, hours_to_expiry=6)


def expiry(hours=24):
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def donation_form(**override):
    form = dict(donor="Hostel Mess", food_type="Cooked rice", quantity_kg="20",
                expiry_time=expiry(), ngo="auto")
    form.update(override)
    return form


def create_donation(client, **override):
    resp = client.post("/donate", data=donation_form(**override))
    assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
    return re.search(r"DON-[0-9A-F]{8}", resp.headers["Location"]).group(0)


def db_rows(appmod, sql, params=()):
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


# ---------------- pages ----------------
def test_homepage_loads(client):
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Food Waste Prediction" in html and 'id="predict-form"' in html
    assert "Chart" not in html and "chart-" not in html   # the old charts are gone


@pytest.mark.parametrize("path", ["/", "/overview", "/donations", "/donate", "/track", "/chain", "/verify",
                                  "/model", "/status"])
def test_pages_load_on_empty_database(client, path):
    assert client.get(path).status_code == 200


def test_static_assets_served(client):
    assert client.get("/static/css/style.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200


def test_unknown_page_gives_friendly_404(client):
    resp = client.get("/nope")
    assert resp.status_code == 404 and "not found" in resp.get_data(as_text=True).lower()
    assert "Traceback" not in resp.get_data(as_text=True)


# ---------------- prediction ----------------
def test_predict_returns_waste_risk_and_recommendation(client):
    resp = client.post("/predict", json=PREDICT)
    data = resp.get_json()
    assert resp.status_code == 200
    assert isinstance(data["waste_kg"], float) and data["waste_kg"] >= 0
    assert data["risk"] in ("LOW", "MEDIUM", "HIGH")
    assert data["decision"] == "DONATE" and data["suggested_ngo"] and data["reason"]


def test_predict_risk_matches_thresholds(client):
    data = client.post("/predict", json=PREDICT).get_json()
    expected = "LOW" if data["waste_kg"] < 5 else "MEDIUM" if data["waste_kg"] <= 15 else "HIGH"
    assert data["risk"] == expected


def test_predict_accepts_form_encoded_data(client):
    resp = client.post("/predict", data=PREDICT)
    assert resp.status_code == 200 and "waste_kg" in resp.get_json()


def test_predict_expired_food_not_donated(client):
    data = client.post("/predict", json={**PREDICT, "hours_to_expiry": 0}).get_json()
    assert data["decision"] == "DO_NOT_DONATE" and data["suggested_ngo"] is None


def test_predict_get_redirects_home(client):
    assert client.get("/predict").status_code == 302


@pytest.mark.parametrize("change", [
    {"meals_served": ""}, {"meals_served": "abc"}, {"meals_served": -10}, {"humidity": 500},
    {"day_of_week": 8}, {"hours_to_expiry": "soon"}, {"hours_to_expiry": -2},
])
def test_predict_rejects_invalid_input(client, change):
    resp = client.post("/predict", json={**PREDICT, **change})
    assert resp.status_code == 400 and resp.get_json()["error"]


def test_predict_rejects_missing_field_and_empty_body(client):
    data = dict(PREDICT)
    del data["temperature"]
    assert client.post("/predict", json=data).status_code == 400
    assert client.post("/predict", json={}).status_code == 400
    assert client.post("/predict", data="garbage", content_type="text/plain").status_code == 400


def test_predictions_are_stored_and_counted(client, appmod):
    client.post("/predict", json=PREDICT)
    client.post("/predict", json=PREDICT)
    assert len(db_rows(appmod, "SELECT * FROM predictions")) == 2
    assert appmod.gather_stats()["predictions"] == 2


def test_missing_model_is_handled(client, appmod, monkeypatch):
    monkeypatch.setattr(appmod, "bundle", None)
    resp = client.post("/predict", json=PREDICT)
    assert resp.status_code == 503 and "train_model.py" in resp.get_json()["error"]
    page = client.get("/")
    assert page.status_code == 200 and "not been trained" in page.get_data(as_text=True)
    status = client.get("/status").get_json()
    assert status["model_available"] is False and status["status"] == "degraded"


# ---------------- donations ----------------
def test_donation_creates_database_row_and_block(client, appmod):
    blocks_before = len(appmod.chain.get_chain())
    code = create_donation(client, quantity_kg="20")
    rows = db_rows(appmod, "SELECT * FROM donations WHERE donation_code=?", (code,))
    assert len(rows) == 1
    assert rows[0]["donor"] == "Hostel Mess" and rows[0]["quantity"] == 20 and rows[0]["status"] == "Pending"
    assert len(appmod.chain.get_chain()) == blocks_before + 1
    block = appmod.chain.donation_history(code)[0]
    assert block.data["event"] == "DONATION_CREATED" and block.data["quantity_kg"] == 20


def test_donor_name_is_not_written_to_blockchain(client, appmod):
    code = create_donation(client, donor="Secret Donor Name")
    block = appmod.chain.donation_history(code)[0]
    assert "Secret Donor Name" not in json.dumps(block.data)
    assert block.data["donor_ref"]


def test_donation_detail_page_shows_trail(client):
    code = create_donation(client)
    html = client.get(f"/donation/{code}").get_data(as_text=True)
    assert code in html and "Delivery progress" in html and "Registered" in html


def test_chain_page_shows_donation_block(client):
    code = create_donation(client)
    html = client.get("/chain").get_data(as_text=True)
    assert code in html and "Record #0" in html and "Record #1" in html


def test_auto_ngo_matching(appmod):
    assert appmod.match_ngo(20)["name"] == "Annadaan Food Foundation"   # smallest that fits (40 kg)
    assert appmod.match_ngo(100)["name"] == "Seva Rasoi Kitchen"        # 120 kg
    assert appmod.match_ngo(10000)["name"] == "City Food Bank"          # nothing fits: largest


@pytest.mark.parametrize("override,fragment", [
    ({"donor": ""}, "Donor"),
    ({"donor": "x" * 200}, "Donor"),
    ({"food_type": "Poison"}, "food type"),
    ({"quantity_kg": "abc"}, "Quantity"),
    ({"quantity_kg": "-5"}, "Quantity"),
    ({"quantity_kg": "0"}, "Quantity"),
    ({"expiry_time": ""}, "expiry"),
    ({"expiry_time": "not-a-date"}, "expiry"),
    ({"expiry_time": expiry(-5)}, "expired"),
    ({"expiry_time": expiry(1)}, "shelf life"),
    ({"ngo": "Fake NGO"}, "valid NGO"),
    ({"ngo": "Annadaan Food Foundation", "quantity_kg": "500"}, "at most"),
])
def test_invalid_donations_are_rejected_and_nothing_is_saved(client, appmod, override, fragment):
    resp = client.post("/donate", data=donation_form(**override))
    assert resp.status_code == 400
    assert fragment.lower() in resp.get_data(as_text=True).lower()
    assert db_rows(appmod, "SELECT * FROM donations") == []
    assert len(appmod.chain.get_chain()) == 1


def test_form_values_are_kept_after_validation_error(client):
    resp = client.post("/donate", data=donation_form(quantity_kg="-1", donor="Keep Me"))
    assert "Keep Me" in resp.get_data(as_text=True)


def test_user_content_is_html_escaped(client):
    code = create_donation(client, donor="<script>alert(1)</script>")
    for url in ("/donations", f"/donation/{code}", "/overview", "/"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in client.get("/donations").get_data(as_text=True)
    assert "&lt;script&gt;" in client.get(f"/donation/{code}").get_data(as_text=True)


def test_sql_injection_attempt_is_harmless(client, appmod):
    create_donation(client, donor="x'); DROP TABLE donations;--")
    assert len(db_rows(appmod, "SELECT * FROM donations")) == 1


def test_status_flow_appends_blocks(client, appmod):
    code = create_donation(client)
    for _ in range(6):  # extra calls are no-ops once the donation is delivered
        client.post(f"/donation/{code}/status")
    assert [b.data["status"] for b in appmod.chain.donation_history(code)] == [
        "Pending", "Accepted", "Picked Up", "In Transit", "Delivered"]
    assert db_rows(appmod, "SELECT status FROM donations")[0]["status"] == "Delivered"
    assert appmod.chain.is_valid()


def test_unknown_donation_returns_404(client):
    assert client.get("/donation/DON-00000000").status_code == 404
    assert client.get("/donation/bad-code").status_code == 404
    assert client.post("/donation/DON-00000000/status").status_code == 404


def test_qr_code_png(client):
    code = create_donation(client)
    resp = client.get(f"/qr/{code}.png")
    assert resp.status_code == 200 and resp.mimetype == "image/png" and resp.data[:4] == b"\x89PNG"
    assert client.get("/qr/DON-00000000.png").status_code == 404


def test_app_works_without_qrcode_package(client, appmod, monkeypatch):
    code = create_donation(client)
    monkeypatch.setattr(appmod, "QR_AVAILABLE", False)
    assert client.get(f"/qr/{code}.png").status_code == 404
    assert "QR codes are unavailable" in client.get(f"/donation/{code}").get_data(as_text=True)


# ---------------- blockchain verification ----------------
def test_verify_page_and_json(client):
    create_donation(client)
    assert "intact" in client.get("/verify").get_data(as_text=True)
    data = client.get("/verify?format=json").get_json()
    assert data["valid"] is True and len(data["blocks"]) == 2


def test_tamper_is_detected_and_restore_fixes_it(client):
    create_donation(client)
    client.post("/tamper", data={"index": "1"})
    page = client.get("/verify").get_data(as_text=True)
    assert "tampering detected" in page and "Block 1 was modified" in page
    assert client.get("/verify?format=json").get_json()["valid"] is False
    assert "Altered" in client.get("/chain").get_data(as_text=True)

    client.post("/restore")
    assert client.get("/verify?format=json").get_json()["valid"] is True
    assert "intact" in client.get("/verify").get_data(as_text=True)


def test_tamper_defaults_to_latest_block(client):
    create_donation(client)
    client.post("/tamper", data={})
    assert client.get("/verify?format=json").get_json()["bad_index"] == 1


@pytest.mark.parametrize("value", ["0", "99", "abc", "-3"])
def test_tamper_rejects_bad_block_numbers(client, value):
    create_donation(client)
    client.post("/tamper", data={"index": value})
    assert client.get("/verify?format=json").get_json()["valid"] is True


def test_tamper_on_empty_chain_does_nothing(client):
    client.post("/tamper", data={})
    assert client.get("/verify?format=json").get_json()["valid"] is True


def test_restore_without_tamper_is_harmless(client):
    assert client.post("/restore").status_code == 302


def test_corrupted_block_does_not_crash_pages(client, appmod):
    create_donation(client)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute('UPDATE blocks SET data=? WHERE "index"=1', ("{broken",))
    conn.commit()
    conn.close()
    for path in ("/", "/chain", "/verify", "/status"):
        assert client.get(path).status_code == 200, path
    assert client.get("/status").get_json()["blockchain_valid"] is False


def test_reset_clears_everything(client, appmod):
    create_donation(client)
    client.post("/predict", json=PREDICT)
    client.post("/reset")
    assert db_rows(appmod, "SELECT * FROM donations") == []
    assert db_rows(appmod, "SELECT * FROM predictions") == []
    assert len(appmod.chain.get_chain()) == 1


# ---------------- status & stats ----------------
def test_status_endpoint(client):
    create_donation(client)
    data = client.get("/status").get_json()
    assert data["status"] == "ok"
    assert data["model_available"] and data["database_available"]
    assert data["donations"] == 1 and data["blockchain_blocks"] == 2 and data["blockchain_valid"] is True


def test_stats_reflect_delivery(client, appmod):
    code = create_donation(client, quantity_kg="20")
    for _ in range(4):
        client.post(f"/donation/{code}/status")
    stats = appmod.gather_stats()
    assert stats["donations"] == 1 and stats["donated_kg"] == 20 and stats["delivered_kg"] == 20
    assert stats["by_status"] == {"Delivered": 1}
    assert stats["delivered"] == 1 and stats["awaiting_pickup"] == 0 and stats["attention"] == 0
    assert client.get("/overview").status_code == 200  # overview renders with donations present


def test_ngos_are_seeded(appmod):
    assert len(appmod.get_ngos()) >= 4


# ---------------- robustness ----------------
def test_unexpected_error_shows_friendly_500_page(client, appmod, monkeypatch):
    def boom():
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(appmod, "gather_stats", boom)
    resp = client.get("/overview")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 500 and "Something went wrong" in html
    assert "secret internal detail" not in html and "Traceback" not in html


def test_chain_page_links_blocks_to_donation_pages(client):
    code = create_donation(client)
    assert f'href="/track/{code}"' in client.get("/chain").get_data(as_text=True)


def test_auto_match_rejects_quantity_larger_than_every_ngo(client, appmod):
    resp = client.post("/donate", data=donation_form(quantity_kg="5000"))
    assert resp.status_code == 400 and "at most" in resp.get_data(as_text=True)
    assert db_rows(appmod, "SELECT * FROM donations") == []


def test_oversized_form_is_rejected(client):
    resp = client.post("/donate", data={"donor": "x" * 200000})
    assert resp.status_code == 413


def test_database_and_directories_are_created_automatically(tmp_path):
    """A brand-new install: the DB file and its folder do not exist yet."""
    import os
    import subprocess
    import sys
    db = tmp_path / "new_folder" / "nested" / "fresh.db"
    script = ("import app, sqlite3; c = sqlite3.connect(app.config.DB_PATH); "
              "print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")))"
              "; print(c.execute('SELECT COUNT(*) FROM ngos').fetchone()[0], c.execute('SELECT COUNT(*) FROM blocks').fetchone()[0])")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", script], cwd=root, check=True, capture_output=True, text=True,
                         env={**os.environ, "FOOD_DB_PATH": str(db)}).stdout
    assert db.exists()
    assert "donations" in out and "ngos" in out and "blocks" in out and "predictions" in out
    assert out.strip().splitlines()[-1] == "5 1"   # 5 seeded NGOs, 1 genesis block
