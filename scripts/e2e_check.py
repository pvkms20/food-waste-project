"""End-to-end check against a RUNNING server (python app.py) using plain HTTP.

Usage: python scripts/e2e_check.py [base_url]   (default http://127.0.0.1:5000)
Follows the demo flow: predict -> donate -> verify -> deliver -> tamper -> detect -> restore.
It starts by resetting the demo data, so run it against a demo database only.
"""
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
DB = Path(__file__).resolve().parent.parent / "database.db"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
failures = []


def call(path, data=None, as_json=False):
    body, headers = None, {}
    if data is not None:
        if as_json:
            body, headers = json.dumps(data).encode(), {"Content-Type": "application/json"}
        else:
            body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        resp = opener.open(req, timeout=20)
    except urllib.error.HTTPError as err:
        resp = err
    return resp.status if hasattr(resp, "status") else resp.code, resp.url if hasattr(resp, "url") else "", resp.read()


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def db(sql, params=()):
    conn = sqlite3.connect(DB)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# 1. clean start
status, _, _ = call("/reset", {})
check("1  reset demo data", status == 200)
check("2  database has genesis block only", db('SELECT COUNT(*) FROM blocks')[0][0] == 1)

# 3. dashboard
status, _, body = call("/")
html = body.decode()
check("3  dashboard loads with prediction form", status == 200 and 'id="predict-form"' in html)
check("4  dashboard shows blockchain Valid", "Valid" in html)

# 5. prediction
payload = dict(meals_served=300, food_prepared_kg=140, kitchen_staff=8, day_of_week=5, temperature=33,
               humidity=70, special_event=1, previous_waste_kg=12, hours_to_expiry=6)
status, _, body = call("/predict", payload, as_json=True)
pred = json.loads(body)
check("5  /predict returns kg, risk, recommendation",
      status == 200 and isinstance(pred["waste_kg"], float) and pred["risk"] in ("LOW", "MEDIUM", "HIGH")
      and pred["decision"] == "DONATE", str(pred))
print(f"      predicted {pred['waste_kg']} kg, risk {pred['risk']}, decision {pred['decision']}, NGO {pred['suggested_ngo']}")
status, _, body = call("/predict", {**payload, "meals_served": -4}, as_json=True)
check("6  invalid prediction input gives a clean 400", status == 400 and "error" in json.loads(body))

# 7. donation
expiry = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
status, final_url, body = call("/donate", dict(donor="E2E Hostel Mess", food_type="Cooked rice",
                                               quantity_kg=pred["donate_kg"], expiry_time=expiry, ngo="auto"))
match = re.search(r"DON-[0-9A-F]{8}", final_url)
code = match.group(0) if match else None
check("7  donation submitted and redirected to its page", status == 200 and code is not None, final_url)
check("8  confirmation shown", "recorded on the blockchain" in body.decode())
rows = db("SELECT donor, quantity, ngo, status FROM donations WHERE donation_code=?", (code,))
check("9  donation row exists in SQLite", len(rows) == 1 and rows[0][3] == "Pending", str(rows))
check("10 a block was created for it", db('SELECT COUNT(*) FROM blocks')[0][0] == 2)

# 11. chain + verify
status, _, body = call("/chain")
check("11 blockchain page shows the donation", status == 200 and code in body.decode())
status, _, body = call("/verify?format=json")
check("12 chain verifies as valid", json.loads(body)["valid"] is True)

# 13. lifecycle
call(f"/donation/{code}/status", {})
call(f"/donation/{code}/status", {})
check("13 status flow reaches Delivered", db("SELECT status FROM donations WHERE donation_code=?", (code,))[0][0] == "Delivered")
check("14 each status change added a block", db('SELECT COUNT(*) FROM blocks')[0][0] == 4)
status, _, body = call(f"/qr/{code}.png")
check("15 QR code PNG served", status == 200 and body[:4] == b"\x89PNG")

# 16. tamper
call("/tamper", {"index": "1"})
status, _, body = call("/verify?format=json")
verdict = json.loads(body)
check("16 tampering is detected", verdict["valid"] is False and verdict["bad_index"] == 1, str(verdict))
status, _, body = call("/verify")
check("17 verify page says Tampered", "Blockchain Tampered" in body.decode())
status, _, body = call("/status")
check("18 /status reports invalid chain", json.loads(body)["blockchain_valid"] is False)

# 19. restore
call("/restore", {})
status, _, body = call("/verify?format=json")
check("19 restore makes the chain valid again", json.loads(body)["valid"] is True)

status, _, body = call("/status")
info = json.loads(body)
check("20 /status healthy", info["status"] == "ok" and info["model_available"] and info["donations"] == 1
      and info["blockchain_blocks"] == 4, str(info))
status, _, body = call("/does-not-exist")
check("21 404 page has no stack trace", status == 404 and "Traceback" not in body.decode())

call("/reset", {})
print()
print("ALL END-TO-END CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
