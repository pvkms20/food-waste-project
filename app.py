"""Flask web application: ML prediction, donation workflow and blockchain traceability."""
import hashlib
import io
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)

import config
import ml
from blockchain import Blockchain
from decision import (MIN_DONATION_KG, MIN_SAFE_HOURS, RISK_LOW_MAX, RISK_MEDIUM_MAX,
                      classify_risk, decide)

try:  # QR codes are optional: the app must keep working without the package
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None
QR_AVAILABLE = qrcode is not None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("foodwaste")

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # forms here are tiny; reject anything large

os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
bundle = ml.load_bundle(config.MODEL_PATH)   # None if the model has not been trained yet
chain = Blockchain(config.DB_PATH)

DONATION_CODE = re.compile(r"^DON-[0-9A-F]{8}$")


# ============================ database helpers ============================
@contextmanager
def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Creates tables and seeds demo NGOs. Safe to call repeatedly."""
    with get_db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS ngos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            location TEXT NOT NULL, capacity REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, donation_code TEXT NOT NULL UNIQUE,
            donor TEXT NOT NULL, food_type TEXT NOT NULL, quantity REAL NOT NULL,
            expiry TEXT NOT NULL, ngo TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            waste_kg REAL NOT NULL, risk TEXT NOT NULL, decision TEXT NOT NULL)""")
        if c.execute("SELECT COUNT(*) FROM ngos").fetchone()[0] == 0:
            c.executemany("INSERT INTO ngos (name, location, capacity) VALUES (?,?,?)", [
                ("Annadaan Food Foundation", "Central Zone", 40),
                ("Hope Meals Trust", "North Zone", 75),
                ("Roti Bank Collective", "South Zone", 60),
                ("Seva Rasoi Kitchen", "East Zone", 120),
                ("City Food Bank", "West Zone", 300),
            ])


init_db()


def reset_demo():
    """Clears donations, predictions and the chain (fresh demo state)."""
    with get_db() as c:
        c.execute("DELETE FROM donations")
        c.execute("DELETE FROM predictions")
    chain.reset()


def get_ngos():
    with get_db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ngos ORDER BY capacity")]


def match_ngo(quantity):
    """Smallest NGO that can take the quantity; if none can, the largest one."""
    ngos = get_ngos()
    fits = [n for n in ngos if n["capacity"] >= quantity]
    return fits[0] if fits else ngos[-1]


def list_donations(limit=None):
    sql = "SELECT * FROM donations ORDER BY id DESC"
    params = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    with get_db() as c:
        return [dict(r) for r in c.execute(sql, params)]


def get_donation(code):
    if not DONATION_CODE.match(code):
        return None
    with get_db() as c:
        row = c.execute("SELECT * FROM donations WHERE donation_code=?", (code,)).fetchone()
    return dict(row) if row else None


def gather_stats():
    with get_db() as c:
        p = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(waste_kg),0) AS kg FROM predictions").fetchone()
        d = c.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(quantity),0) AS kg,
                         COALESCE(SUM(CASE WHEN status='Delivered' THEN quantity END),0) AS delivered
                         FROM donations""").fetchone()
        by_status = {r["status"]: r["n"] for r in
                     c.execute("SELECT status, COUNT(*) AS n FROM donations GROUP BY status")}
    return {"predictions": p["n"], "predicted_kg": round(p["kg"], 1), "donations": d["n"],
            "donated_kg": round(d["kg"], 1), "delivered_kg": round(d["delivered"], 1),
            "by_status": by_status}


# ============================ helpers ============================
def parse_hours(raw):
    """Hours until expiry from the prediction form (default 6)."""
    value = raw.get("hours_to_expiry")
    if value is None or str(value).strip() == "":
        return 6.0
    try:
        hours = float(value)
    except (TypeError, ValueError):
        raise ml.InputError("Hours until expiry must be a number.")
    if hours < 0 or hours > 24 * 30:
        raise ml.InputError("Hours until expiry must be between 0 and 720.")
    return hours


def parse_expiry(text):
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def validate_donation(form):
    """Returns (clean_dict, error_message). Exactly one of them is None."""
    donor = (form.get("donor") or "").strip()
    if not donor or len(donor) > 80:
        return None, "Donor name is required (maximum 80 characters)."
    food_type = form.get("food_type") or ""
    if food_type not in config.FOOD_TYPES:
        return None, "Please choose a valid food type."
    try:
        quantity = round(float(form.get("quantity_kg", "")), 1)
    except (TypeError, ValueError):
        return None, "Quantity must be a number."
    if not 0 < quantity <= 100000:
        return None, "Quantity must be greater than 0 kg."
    expiry = parse_expiry(form.get("expiry_time") or "")
    if expiry is None:
        return None, "Please enter a valid expiry date and time."
    hours = (expiry - datetime.now()).total_seconds() / 3600
    if hours <= 0:
        return None, "This food has already expired and cannot be donated."
    if hours < MIN_SAFE_HOURS:
        return None, (f"Food must have at least {MIN_SAFE_HOURS:g} hours of shelf life left "
                      f"(this one has {hours:.1f} h).")
    ngo_name = form.get("ngo") or "auto"
    if ngo_name == "auto":
        ngo = match_ngo(quantity)
    else:
        ngo = next((n for n in get_ngos() if n["name"] == ngo_name), None)
        if ngo is None:
            return None, "Please choose a valid NGO."
    if quantity > ngo["capacity"]:
        return None, f"{ngo['name']} can take at most {ngo['capacity']:g} kg. Choose a larger NGO or auto-match."
    return {"donor": donor, "food_type": food_type, "quantity": quantity,
            "expiry": expiry.strftime("%Y-%m-%d %H:%M"), "ngo": ngo["name"]}, None


def block_payload(donation, event):
    """What goes on the chain. The donor's name stays in SQLite only; the chain gets a short
    one-way reference, so the public ledger holds no personal information."""
    return {
        "event": event,
        "donation_code": donation["donation_code"],
        "donor_ref": hashlib.sha256(donation["donor"].encode("utf-8")).hexdigest()[:10],
        "food_type": donation["food_type"],
        "quantity_kg": donation["quantity"],
        "expiry": donation["expiry"],
        "ngo": donation["ngo"],
        "status": donation["status"],
    }


# ============================ pages ============================
@app.route("/")
def index():
    stats = gather_stats()
    with get_db() as c:
        history = [dict(r) for r in c.execute(
            "SELECT waste_kg, created_at FROM predictions ORDER BY id DESC LIMIT 10")][::-1]
    chart_data = {
        "days": [d[:3] for d in config.DAYS],
        "weekday_avg": bundle["weekday_avg"] if bundle else [],
        "importances": bundle["importances"] if bundle else {},
        "history_labels": [h["created_at"][11:16] for h in history],
        "history_values": [h["waste_kg"] for h in history],
        "status_counts": [stats["by_status"].get(s, 0) for s in ("Pending", "Picked Up", "Delivered")],
    }
    return render_template(
        "index.html", days=config.DAYS, stats=stats, bundle=bundle, chart_data=chart_data,
        verdict=chain.verify(), recent=list_donations(5),
        rules={"low": RISK_LOW_MAX, "medium": RISK_MEDIUM_MAX,
               "min_kg": MIN_DONATION_KG, "min_hours": MIN_SAFE_HOURS})


@app.route("/predict", methods=["GET", "POST"])
def predict():
    if request.method == "GET":
        return redirect(url_for("index"))
    if bundle is None:
        return jsonify(error="The prediction model is not available. Run: python train_model.py"), 503
    raw = request.get_json(silent=True)
    if raw is None:
        raw = request.form.to_dict()
    try:
        inputs = ml.validate_inputs(raw)
        hours = parse_hours(raw)
    except ml.InputError as exc:
        return jsonify(error=str(exc)), 400
    try:
        waste = ml.predict_waste(bundle, inputs)
    except Exception:
        log.exception("Prediction failed")
        return jsonify(error="Prediction failed. Please check the inputs and try again."), 500

    risk = classify_risk(waste)
    ngo = match_ngo(waste)
    outcome = decide(waste, hours, ngo["capacity"], risk)
    with get_db() as c:
        c.execute("INSERT INTO predictions (created_at, waste_kg, risk, decision) VALUES (?,?,?,?)",
                  (datetime.now().isoformat(timespec="seconds"), waste, risk, outcome["decision"]))
    return jsonify({"waste_kg": waste, "risk": risk, "decision": outcome["decision"],
                    "reason": outcome["reason"], "warning": outcome["warning"],
                    "donate_kg": outcome["donate_kg"],
                    "suggested_ngo": ngo["name"] if outcome["decision"] == "DONATE" else None})


@app.route("/donate", methods=["GET", "POST"])
def donate():
    form = {}
    if request.method == "POST":
        form = request.form
        clean, error = validate_donation(form)
        if error:
            flash(error, "danger")
            return render_template("donate.html", ngos=get_ngos(), food_types=config.FOOD_TYPES,
                                   donations=list_donations(), flow=config.STATUS_FLOW, form=form), 400
        code = "DON-" + uuid.uuid4().hex[:8].upper()
        donation = {**clean, "donation_code": code, "status": "Pending"}
        with get_db() as c:
            c.execute("""INSERT INTO donations (donation_code, donor, food_type, quantity, expiry, ngo,
                         status, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                      (code, donation["donor"], donation["food_type"], donation["quantity"],
                       donation["expiry"], donation["ngo"], "Pending",
                       datetime.now().isoformat(timespec="seconds")))
        try:
            chain.add_block(block_payload(donation, "DONATION_CREATED"))
        except Exception:
            log.exception("Could not add block; rolling back donation %s", code)
            with get_db() as c:
                c.execute("DELETE FROM donations WHERE donation_code=?", (code,))
            flash("Could not record the donation on the blockchain. Nothing was saved.", "danger")
            return redirect(url_for("donate"))
        flash(f"Donation {code} saved and recorded on the blockchain.", "success")
        return redirect(url_for("donation_detail", code=code))

    form = {"quantity_kg": request.args.get("qty", "")}
    return render_template("donate.html", ngos=get_ngos(), food_types=config.FOOD_TYPES,
                           donations=list_donations(), flow=config.STATUS_FLOW, form=form)


@app.route("/donation/<code>")
def donation_detail(code):
    donation = get_donation(code)
    if donation is None:
        abort(404)
    return render_template("donation.html", donation=donation, history=chain.donation_history(code),
                           verdict=chain.verify(), qr_available=QR_AVAILABLE, flow=config.STATUS_FLOW)


@app.route("/donation/<code>/status", methods=["POST"])
def update_status(code):
    donation = get_donation(code)
    if donation is None:
        abort(404)
    nxt = config.STATUS_FLOW.get(donation["status"])
    if nxt is None:
        flash("This donation is already delivered.", "info")
    else:
        donation["status"] = nxt
        with get_db() as c:
            c.execute("UPDATE donations SET status=? WHERE donation_code=?", (nxt, code))
        chain.add_block(block_payload(donation, "STATUS_UPDATE"))  # a NEW block; old blocks never change
        flash(f"Status updated to {nxt} (new block added to the chain).", "success")
    return redirect(request.referrer or url_for("donation_detail", code=code))


@app.route("/qr/<code>.png")
def qr_image(code):
    if not QR_AVAILABLE or get_donation(code) is None:
        abort(404)
    url = request.host_url.rstrip("/") + url_for("donation_detail", code=code)
    buf = io.BytesIO()
    qrcode.make(url).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ============================ blockchain ============================
@app.route("/chain")
def chain_view():
    verdict = chain.verify()
    status_by_index = {b["index"]: b for b in verdict["blocks"]}
    return render_template("chain.html", blocks=chain.get_chain(), verdict=verdict,
                           checks=status_by_index)


@app.route("/verify")
def verify():
    verdict = chain.verify()
    if request.args.get("format") == "json":
        return jsonify(verdict)
    return render_template("verify.html", verdict=verdict, blocks=chain.get_chain())


@app.route("/tamper", methods=["POST"])
def tamper():
    """Demo Tamper Test: edits a stored block so /verify can demonstrate detection."""
    raw = request.form.get("index", "").strip()
    try:
        index = int(raw) if raw else None
    except ValueError:
        flash("Block number must be a whole number.", "warning")
        return redirect(url_for("chain_view"))
    target = chain.tamper(index)
    if target is None:
        flash("Pick an existing block other than the genesis block (create a donation first).", "warning")
        return redirect(url_for("chain_view"))
    flash(f"Demo Tamper Test: block {target} was edited directly in the database. Now run Verify.", "warning")
    return redirect(url_for("verify"))


@app.route("/restore", methods=["POST"])
def restore():
    count = chain.restore()
    flash(f"Restored {count} block(s) to their original data." if count
          else "Nothing to restore: no demo tampering is active.", "info")
    return redirect(url_for("chain_view"))


@app.route("/reset", methods=["POST"])
def reset():
    reset_demo()
    flash("Demo data cleared. A fresh blockchain was created.", "info")
    return redirect(url_for("chain_view"))


# ============================ status & errors ============================
@app.route("/status")
def status():
    try:
        with get_db() as c:
            c.execute("SELECT 1").fetchone()
        db_ok = True
    except sqlite3.Error:
        db_ok = False
    verdict = chain.verify() if db_ok else {"valid": False, "message": "Database unavailable"}
    return jsonify({
        "status": "ok" if (db_ok and bundle is not None) else "degraded",
        "model_available": bundle is not None,
        "model_name": bundle["model_name"] if bundle else None,
        "database_available": db_ok,
        "donations": gather_stats()["donations"] if db_ok else 0,
        "blockchain_blocks": len(chain.get_chain()) if db_ok else 0,
        "blockchain_valid": verdict["valid"],
        "blockchain_message": verdict["message"],
        "qr_available": QR_AVAILABLE,
    })


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="That page or record was not found."), 404


@app.errorhandler(413)
def too_large(_):
    return render_template("error.html", code=413, message="The submitted form was too large."), 413


@app.errorhandler(500)
def server_error(exc):
    log.exception("Unhandled error: %s", exc)
    return render_template("error.html", code=500,
                           message="Something went wrong on our side. Please try again."), 500


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
