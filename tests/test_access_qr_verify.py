"""Access control, QR codes (decoded for real), public tracking privacy, verification, search, /status."""
import io
import sqlite3

import numpy as np
import pytest
from PIL import Image

import config
from test_app import create_donation, db_rows

REMOTE = {"REMOTE_ADDR": "192.168.1.50"}
HTML = {"Accept": "text/html"}


def decode_qr(png_bytes):
    cv2 = pytest.importorskip("cv2", reason="opencv is needed to decode QR codes in tests")
    img = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return text


# ---------------- access control ----------------
@pytest.mark.parametrize("method,path", [
    ("post", "/donate"), ("get", "/donate"), ("post", "/tamper"), ("post", "/restore"), ("post", "/reset"),
    ("post", "/donation/DON-00000000/status"),
])
def test_operator_actions_are_blocked_for_other_computers(client, method, path):
    resp = getattr(client, method)(path, environ_overrides=REMOTE)
    assert resp.status_code == 403
    assert "only available on the computer" in resp.get_data(as_text=True)


def test_blocked_remote_actions_change_nothing(client, appmod):
    code = create_donation(client)
    client.post(f"/donation/{code}/status", data={"to": "Accepted"}, environ_overrides=REMOTE)
    client.post("/tamper", environ_overrides=REMOTE)
    client.post("/reset", environ_overrides=REMOTE)
    assert appmod.get_donation(code)["status"] == "Pending" and appmod.chain.is_valid()


def test_remote_visitors_can_read_public_pages_but_see_no_controls(client):
    code = create_donation(client)
    for path in (f"/track/{code}", f"/verify?code={code}", "/overview", "/donations", "/chain", "/"):
        assert client.get(path, environ_overrides=REMOTE, headers=HTML).status_code == 200, path
    detail = client.get(f"/donation/{code}", environ_overrides=REMOTE).get_data(as_text=True)
    assert "Update status" not in detail
    chain_html = client.get("/chain", environ_overrides=REMOTE).get_data(as_text=True)
    assert "Demo tools" not in chain_html and "New donation" not in client.get("/donations", environ_overrides=REMOTE).get_data(as_text=True)


def test_local_operator_sees_controls(client):
    code = create_donation(client)
    assert "Update status" in client.get(f"/donation/{code}").get_data(as_text=True)
    assert "Demo tools" in client.get("/chain").get_data(as_text=True)


def test_remote_operator_override(client, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_REMOTE_OPERATOR", True)
    assert client.get("/donate", environ_overrides=REMOTE).status_code == 200


# ---------------- QR ----------------
def test_qr_decodes_to_the_public_tracking_url(client):
    code = create_donation(client)
    png = client.get(f"/qr/{code}.png").data
    assert decode_qr(png) == f"http://localhost/track/{code}"


def test_qr_target_opens_and_is_public_and_private(client):
    code = create_donation(client, donor="Very Private Donor")
    url = decode_qr(client.get(f"/qr/{code}.png").data)
    path = url.replace("http://localhost", "")
    page = client.get(path, environ_overrides=REMOTE, headers=HTML)
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and code in html
    assert "Very Private Donor" not in html          # donor name never appears on the public page
    assert "Update status" not in html and "<form" not in html.split("</aside>")[-1].split("<footer")[0]


def test_public_base_url_overrides_the_qr_host(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://food.example.org")
    code = create_donation(client)
    assert decode_qr(client.get(f"/qr/{code}.png").data) == f"https://food.example.org/track/{code}"
    assert "points to this computer only" not in client.get(f"/donation/{code}").get_data(as_text=True)


def test_localhost_qr_warns_that_phones_cannot_open_it(client):
    code = create_donation(client)
    assert "points to this computer only" in client.get(f"/donation/{code}").get_data(as_text=True)


def test_lan_address_used_when_server_listens_on_all_interfaces(client, monkeypatch, appmod):
    monkeypatch.setattr(config, "BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(appmod, "lan_ip", lambda: "192.168.1.7")
    code = create_donation(client)
    assert decode_qr(client.get(f"/qr/{code}.png", base_url="http://127.0.0.1:5000").data) \
        == f"http://192.168.1.7:5000/track/{code}"


# ---------------- tracking pages ----------------
def test_track_search_flow(client):
    code = create_donation(client)
    resp = client.get(f"/track?code={code.lower()}")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/track/{code}")
    assert client.get("/track?code=DON-00000000").status_code == 404
    bad = client.get("/track?code=%3Cscript%3E")
    assert bad.status_code == 400 and "&lt;SCRIPT&gt;" in bad.get_data(as_text=True)   # echoed, but escaped
    assert "<SCRIPT>" not in bad.get_data(as_text=True)
    assert client.get("/track/DON-00000000").status_code == 404


# ---------------- verification ----------------
def test_verify_donation_states(client, appmod):
    code = create_donation(client)
    assert appmod.verify_donation(code)["state"] == "verified"
    assert appmod.verify_donation("DON-00000000")["state"] == "not_found"
    assert "Verified" in client.get(f"/verify?code={code}").get_data(as_text=True)
    data = client.get(f"/verify?code={code}&format=json").get_json()
    assert data["valid"] is True and data["donation"]["state"] == "verified"
    assert client.get("/verify?code=bad").status_code == 400
    assert client.get("/verify?code=bad&format=json").get_json()["error"]


def test_tampering_with_this_donations_block_is_flagged(client, appmod):
    code = create_donation(client)
    client.post("/tamper", data={"index": "1"})
    res = appmod.verify_donation(code)
    assert res["state"] == "tampered" and not res["valid"]
    assert "tampering" in client.get(f"/verify?code={code}").get_data(as_text=True).lower()
    assert "Possible tampering" in client.get(f"/track/{code}").get_data(as_text=True)
    client.post("/restore")
    assert appmod.verify_donation(code)["state"] == "verified"


def test_tampering_elsewhere_is_reported_as_chain_issue(client, appmod):
    a = create_donation(client)
    b = create_donation(client)
    client.post("/tamper", data={"index": "2"})            # block of donation b
    assert appmod.verify_donation(b)["state"] == "tampered"
    assert appmod.verify_donation(a)["state"] == "chain_issue"
    assert "another part of the" in appmod.verify_donation(a)["message"]


def test_database_edit_that_disagrees_with_the_chain_is_caught(client, appmod):
    code = create_donation(client)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE donations SET status='Delivered' WHERE donation_code=?", (code,))
    conn.commit(); conn.close()
    res = appmod.verify_donation(code)
    assert res["state"] == "tampered"
    assert any(not c["ok"] and "Current status" in c["name"] for c in res["checks"])


def test_edited_quantity_in_database_is_caught(client, appmod):
    code = create_donation(client, quantity_kg="20")
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE donations SET quantity=500 WHERE donation_code=?", (code,))
    conn.commit(); conn.close()
    assert appmod.verify_donation(code)["state"] == "tampered"


def test_forged_event_history_is_caught(client, appmod):
    code = create_donation(client)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("INSERT INTO donation_events (donation_code, status, note, created_at, block_index) VALUES (?,?,?,?,?)",
                 (code, "Delivered", "", "2026-01-01T00:00:00", None))
    conn.commit(); conn.close()
    assert appmod.verify_donation(code)["state"] == "tampered"


# ---------------- donations list ----------------
def test_search_and_status_filter(client):
    a = create_donation(client, donor="Alpha Hostel", food_type="Cooked rice")
    b = create_donation(client, donor="Beta Canteen", food_type="Fruits")
    client.post(f"/donation/{b}/status?format=json", json={"to": "Accepted"})
    page = lambda q: client.get(q).get_data(as_text=True)
    assert a in page("/donations?q=alpha") and b not in page("/donations?q=alpha")
    assert b in page("/donations?q=fruits") and a not in page("/donations?q=fruits")
    assert b in page(f"/donations?q={b}")
    assert b in page("/donations?status=Accepted") and a not in page("/donations?status=Accepted")
    assert "No donations match" in page("/donations?q=zzzz")
    assert a in page("/donations?status=bogus") and b in page("/donations?status=bogus")   # unknown filter ignored


@pytest.mark.parametrize("q", ["%", "_", "' OR 1=1 --", "a" * 500, "\\", "<script>alert(1)</script>"])
def test_search_is_injection_safe(client, q):
    create_donation(client)
    resp = client.get("/donations", query_string={"q": q})
    assert resp.status_code == 200 and "<script>alert(1)</script>" not in resp.get_data(as_text=True)
    assert len(db_rows(None, "SELECT * FROM donations")) == 1


def test_percent_wildcard_is_literal(client):
    create_donation(client)
    assert "No donations match" in client.get("/donations?q=%25").get_data(as_text=True)


# ---------------- /status ----------------
def test_status_page_for_browsers_and_json_for_programs(client):
    page = client.get("/status", headers=HTML)
    assert page.mimetype == "text/html" and "System status" in page.get_data(as_text=True)
    assert client.get("/status", headers={"Accept": "application/json"}).get_json()["status"] == "ok"
    assert client.get("/status?format=json", headers=HTML).get_json()["blockchain_valid"] is True
    assert client.get("/status").get_json()["status"] == "ok"      # curl-style request


def test_status_page_reports_a_problem(client, appmod, monkeypatch):
    monkeypatch.setattr(appmod, "bundle", None)
    html = client.get("/status", headers=HTML).get_data(as_text=True)
    assert "Degraded" in html and "Not found" in html


def test_navigation_has_every_section(client):
    html = client.get("/").get_data(as_text=True)
    for label in ("Primary workspace", "Donation management", "Trust and verification", "Overview", "Waste Prediction",
                  "Donations", "Track Donation", "Verify Donation", "Trust &amp; Records"):
        assert label in html, label
    assert 'aria-current="page"' in html and "navbar" not in html
    for path, label in (("/overview", "Overview"), ("/donations", "Donations"), ("/track", "Track Donation")):
        page = client.get(path).get_data(as_text=True)
        assert f'aria-label="{label}"\n           aria-current="page"' in page, path
