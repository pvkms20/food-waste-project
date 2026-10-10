"""Flask web application: ML prediction, donation workflow and blockchain traceability."""
import functools
import hashlib
import io
import logging
import os
import re
import socket
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)

import config
import ml
import tracking
from icons import icon
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
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
NOTE_MAX = 200


# ============================ database helpers ============================
@contextmanager
def get_db():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    """Creates tables, migrates older databases and seeds demo NGOs. Safe to call repeatedly."""
    with get_db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS ngos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            location TEXT NOT NULL, capacity REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, donation_code TEXT NOT NULL UNIQUE,
            donor TEXT NOT NULL, food_type TEXT NOT NULL, quantity REAL NOT NULL,
            expiry TEXT NOT NULL, ngo TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT, collected_kg REAL, delivered_kg REAL)""")
        # older databases: add the columns introduced with the tracking timeline
        have = {r["name"] for r in c.execute("PRAGMA table_info(donations)")}
        if "updated_at" not in have:
            c.execute("ALTER TABLE donations ADD COLUMN updated_at TEXT")
        if "collected_kg" not in have:
            c.execute("ALTER TABLE donations ADD COLUMN collected_kg REAL")
        if "delivered_kg" not in have:
            c.execute("ALTER TABLE donations ADD COLUMN delivered_kg REAL")
        c.execute("""CREATE TABLE IF NOT EXISTS donation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, donation_code TEXT NOT NULL, status TEXT NOT NULL,
            note TEXT, created_at TEXT NOT NULL, block_index INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_code ON donation_events (donation_code)")
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
    backfill_events()


def backfill_events():
    """Older databases have chain blocks but no donation_events rows: rebuild them from the chain."""
    with get_db() as c:
        have = {r["donation_code"] for r in c.execute("SELECT DISTINCT donation_code FROM donation_events")}
        codes = [r["donation_code"] for r in c.execute("SELECT donation_code FROM donations")]
    missing = [code for code in codes if code not in have]
    if not missing:
        return
    blocks = chain.get_chain()
    with get_db() as c:
        for code in missing:
            for b in blocks:
                if b.data.get("donation_code") == code:
                    c.execute("INSERT INTO donation_events (donation_code, status, note, created_at, block_index)"
                              " VALUES (?,?,?,?,?)", (code, b.data.get("status", "Pending"), "", b.timestamp, b.index))


init_db()


def reset_demo():
    """Clears donations, events, predictions and the chain (fresh demo state)."""
    with get_db() as c:
        c.execute("DELETE FROM donations")
        c.execute("DELETE FROM donation_events")
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


def _decorate(row):
    d = dict(row)
    d["updated_at"] = d.get("updated_at") or d["created_at"]
    d["info"] = config.STATUS_INFO.get(d["status"], {"label": d["status"], "text": "", "actor": ""})
    d["needs_attention"] = needs_attention(d)
    return d


def needs_attention(d):
    """Failed or rejected donations, and active donations whose food has passed its expiry time."""
    if d["status"] in ("Failed", "Rejected"):
        return True
    if d["status"] in config.TERMINAL_STATUSES:
        return False
    try:
        return datetime.strptime(d["expiry"], "%Y-%m-%d %H:%M") < datetime.now()
    except ValueError:
        return False


def list_donations(limit=None, query="", status=""):
    sql, params, where = "SELECT * FROM donations", [], []
    query = (query or "").strip()[:80]
    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(donation_code LIKE ? ESCAPE '\\' OR donor LIKE ? ESCAPE '\\' OR food_type LIKE ? ESCAPE '\\'"
                     " OR ngo LIKE ? ESCAPE '\\')")
        params += [like] * 4
    if status in config.ALL_STATUSES:
        where.append("status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with get_db() as c:
        return [_decorate(r) for r in c.execute(sql, params)]


def get_donation(code):
    if not DONATION_CODE.match(code or ""):
        return None
    with get_db() as c:
        row = c.execute("SELECT * FROM donations WHERE donation_code=?", (code,)).fetchone()
    return _decorate(row) if row else None


def get_events(code):
    with get_db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM donation_events WHERE donation_code=? ORDER BY id", (code,))]


def gather_stats():
    with get_db() as c:
        p = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(waste_kg),0) AS kg FROM predictions").fetchone()
        d = c.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(quantity),0) AS kg,
                         COALESCE(SUM(CASE WHEN status='Delivered'
                                      THEN COALESCE(delivered_kg, quantity) END),0) AS delivered
                         FROM donations""").fetchone()
        by_status = {r["status"]: r["n"] for r in
                     c.execute("SELECT status, COUNT(*) AS n FROM donations GROUP BY status")}
        rows = [dict(r) for r in c.execute("SELECT status, expiry FROM donations")]
    attention = sum(1 for r in rows if needs_attention(r))
    return {"predictions": p["n"], "predicted_kg": round(p["kg"], 1), "donations": d["n"],
            "donated_kg": round(d["kg"], 1), "delivered_kg": round(d["delivered"], 1),
            "by_status": by_status,
            "awaiting_pickup": by_status.get("Pending", 0) + by_status.get("Accepted", 0),
            "in_transit": by_status.get("Picked Up", 0) + by_status.get("In Transit", 0),
            "delivered": by_status.get("Delivered", 0), "attention": attention}


# ============================ helpers ============================
def is_local_request():
    return (request.remote_addr or "") in LOOPBACK


def can_operate():
    return is_local_request() or config.ALLOW_REMOTE_OPERATOR


def operator_only(view):
    """Create / update / demo actions are limited to the computer running the app.
    Set FOOD_ALLOW_REMOTE_OPERATOR=1 to lift the limit (not recommended: there is no login)."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not can_operate():
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def wants_html():
    """Browsers send text/html in Accept. Programs (curl, fetch with defaults) get JSON."""
    return "text/html" in request.headers.get("Accept", "")


def wants_json():
    return request.is_json or request.args.get("format") == "json" or not wants_html()


def lan_ip():
    """This computer's LAN address (no packet is sent; UDP connect only selects the interface)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def public_base():
    """(base_url, reachable_from_phone, hint). The QR code points at this address."""
    if config.PUBLIC_BASE_URL:
        return config.PUBLIC_BASE_URL, True, ""
    base = request.host_url.rstrip("/")
    parsed = urlparse(base)
    if (parsed.hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        return base, True, ""
    if config.BIND_HOST in ("0.0.0.0", "::"):
        ip = lan_ip()
        if ip:
            return f"{parsed.scheme}://{ip}:{parsed.port or 80}", True, ""
    return base, False, ("This QR code points to this computer only (localhost). To scan it with a phone on "
                         "the same Wi-Fi, start the app with HOST=0.0.0.0 and set PUBLIC_BASE_URL to "
                         "http://YOUR-LAN-IP:5000 (see the README).")


def track_url(code):
    return public_base()[0] + url_for("track_detail", code=code)


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


def block_payload(donation, event, note="", **extra):
    """What goes on the chain. The donor's name stays in SQLite only; the chain gets a short
    one-way reference, so the public ledger holds no personal information."""
    data = {
        "event": event,
        "donation_code": donation["donation_code"],
        "donor_ref": hashlib.sha256(donation["donor"].encode("utf-8")).hexdigest()[:10],
        "food_type": donation["food_type"],
        "quantity_kg": donation["quantity"],
        "expiry": donation["expiry"],
        "ngo": donation["ngo"],
        "status": donation["status"],
    }
    if note:
        data["note"] = note
    data.update({k: v for k, v in extra.items() if v is not None})
    return data


# ============================ lifecycle ============================
class TransitionError(Exception):
    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


def _kg(raw, label):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = round(float(raw), 1)
    except (TypeError, ValueError):
        raise TransitionError(f"{label} must be a number.")
    if not value > 0:
        raise TransitionError(f"{label} must be greater than 0.")
    return value


def change_status(code, target, note="", collected_kg=None, delivered_kg=None):
    """Moves a donation to `target` if the lifecycle allows it. Returns the updated donation.

    Safe against double clicks and concurrent requests: the UPDATE only succeeds if the status is
    still what we read (compare-and-swap), so a transition can be applied at most once.
    """
    donation = get_donation(code)
    if donation is None:
        raise TransitionError("Donation not found.", 404)
    if target not in config.ALL_STATUSES:
        raise TransitionError("Unknown status.")
    current = donation["status"]
    if target == current:
        raise TransitionError(f"This donation is already '{target}'. Nothing was changed.", 409)
    if not tracking.can_transition(current, target):
        allowed = tracking.allowed_next(current)
        hint = f" Allowed next: {', '.join(allowed)}." if allowed else " This donation is finished."
        raise TransitionError(f"A donation that is '{current}' cannot become '{target}'.{hint}", 409)

    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        raise TransitionError(f"Note is too long (maximum {NOTE_MAX} characters).")
    collected = _kg(collected_kg, "Collected quantity")
    delivered = _kg(delivered_kg, "Delivered quantity")
    if target == "Picked Up":
        collected = collected if collected is not None else donation["quantity"]
        if collected > donation["quantity"]:
            raise TransitionError("Collected quantity cannot be more than the quantity offered.")
    elif collected is not None:
        raise TransitionError("Collected quantity can only be set when marking Picked up.")
    if target == "Delivered":
        base = donation["collected_kg"] if donation["collected_kg"] is not None else donation["quantity"]
        delivered = delivered if delivered is not None else base
        if delivered > base:
            raise TransitionError("Delivered quantity cannot be more than the quantity collected.")
    elif delivered is not None:
        raise TransitionError("Delivered quantity can only be set when marking Delivered.")

    stamp = now_iso()
    with get_db() as c:
        cur = c.execute(
            """UPDATE donations SET status=?, updated_at=?, collected_kg=COALESCE(?, collected_kg),
               delivered_kg=COALESCE(?, delivered_kg) WHERE donation_code=? AND status=?""",
            (target, stamp, collected, delivered, code, current))
        if cur.rowcount == 0:
            raise TransitionError("This donation was just updated by someone else. Reload and try again.", 409)
    updated = {**donation, "status": target}
    try:
        block = chain.add_block(block_payload(updated, "STATUS_UPDATE", note,
                                              collected_kg=collected, delivered_kg=delivered))
    except Exception:
        log.exception("Could not add block for %s; reverting status", code)
        with get_db() as c:
            c.execute("UPDATE donations SET status=?, updated_at=? WHERE donation_code=? AND status=?",
                      (current, donation["updated_at"], code, target))
        raise TransitionError("Could not record the change on the blockchain. Nothing was changed.", 500)
    with get_db() as c:
        c.execute("INSERT INTO donation_events (donation_code, status, note, created_at, block_index)"
                  " VALUES (?,?,?,?,?)", (code, target, note, block.timestamp, block.index))
    return get_donation(code)


def verify_donation(code):
    """Checks one donation: its own blocks, the whole chain, and that the database agrees with the chain.

    state: verified | tampered | chain_issue | not_found
    """
    donation = get_donation(code)
    if donation is None:
        return {"state": "not_found", "valid": False, "code": code, "checks": [], "blocks": [],
                "message": "No donation with this reference exists in this system."}
    verdict = chain.verify()
    per_block = {b["index"]: b for b in verdict["blocks"]}
    blocks = chain.donation_history(code)
    events = get_events(code)
    checks = []

    def add(name, ok, detail_ok, detail_bad):
        checks.append({"name": name, "ok": bool(ok), "detail": detail_ok if ok else detail_bad})

    add("Records exist on the blockchain", bool(blocks),
        f"{len(blocks)} record(s) found", "No blockchain records for this donation")
    own_ok = bool(blocks) and all(per_block.get(b.index, {}).get("hash_ok") and
                                  per_block.get(b.index, {}).get("link_ok") for b in blocks)
    add("Each record matches its fingerprint and link", own_ok,
        "All of this donation's records are intact", "At least one of this donation's records was altered")
    first = blocks[0].data if blocks else {}
    add("First record is the original registration",
        bool(blocks) and first.get("event") == "DONATION_CREATED"
        and first.get("quantity_kg") == donation["quantity"] and first.get("ngo") == donation["ngo"]
        and first.get("food_type") == donation["food_type"],
        "Quantity, food type and NGO match the original record",
        "The registration details differ from the original record")
    chain_statuses = [b.data.get("status") for b in blocks]
    event_statuses = [e["status"] for e in events]
    add("Status history agrees with the blockchain", chain_statuses == event_statuses,
        "Database history and blockchain history are identical", "The stored history differs from the blockchain")
    steps_ok = bool(chain_statuses) and all(tracking.can_transition(a, b)
                                            for a, b in zip(chain_statuses, chain_statuses[1:]))
    add("Every status change followed the allowed order", steps_ok,
        "No invalid jumps in the history", "The history contains a step that is not allowed")
    add("Current status matches the last record",
        bool(chain_statuses) and chain_statuses[-1] == donation["status"],
        f"Current status is {donation['status']}", "The current status differs from the last blockchain record")
    donation_checks = list(checks)
    add("Whole blockchain is intact", verdict["valid"], verdict["message"], verdict["message"])

    if all(c["ok"] for c in checks):
        state, msg = "verified", "Verified: this donation's history matches the tamper-evident record."
    elif all(c["ok"] for c in donation_checks):
        state, msg = "chain_issue", ("This donation's own records are intact, but another part of the "
                                     "blockchain has been altered. See Trust & Records.")
    else:
        state, msg = "tampered", ("Warning: this donation's record does not match the blockchain. "
                                  "It may have been altered.")
    return {"state": state, "valid": state == "verified", "code": code, "checks": checks, "message": msg,
            "blocks": [b.to_dict() for b in blocks]}


@app.context_processor
def inject_globals():
    return {"operator": can_operate(), "status_info": config.STATUS_INFO, "icon": icon}


# ============================ pages ============================
@app.route("/")
def index():
    """Waste Prediction: the main screen."""
    return render_template("predict.html", days=config.DAYS, bundle=bundle,
                           rules={"low": RISK_LOW_MAX, "medium": RISK_MEDIUM_MAX,
                                  "min_kg": MIN_DONATION_KG, "min_hours": MIN_SAFE_HOURS})


@app.route("/overview")
def overview():
    stats = gather_stats()
    return render_template("overview.html", stats=stats, verdict=chain.verify(), recent=list_donations(5),
                           attention=[d for d in list_donations() if d["needs_attention"]][:5])


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
        edible_pct = ml.parse_edible_percent(raw)
    except ml.InputError as exc:
        return jsonify(error=str(exc)), 400
    try:
        waste = ml.predict_waste(bundle, inputs)
    except Exception:
        log.exception("Prediction failed")
        return jsonify(error="Prediction failed. Please check the inputs and try again."), 500

    risk = classify_risk(waste)
    edible = round(waste * edible_pct / 100, 1)
    ngo = match_ngo(edible)
    outcome = decide(edible, hours, ngo["capacity"], risk)
    interval = ml.predict_interval(bundle, waste, inputs["food_prepared_kg"])
    with get_db() as c:
        c.execute("INSERT INTO predictions (created_at, waste_kg, risk, decision) VALUES (?,?,?,?)",
                  (now_iso(), waste, risk, outcome["decision"]))
    donate = outcome["decision"] == "DONATE"
    return jsonify({"waste_kg": waste, "risk": risk, "decision": outcome["decision"],
                    "reason": outcome["reason"], "warning": outcome["warning"],
                    "interval": list(interval) if interval else None,
                    "interval_level": (bundle.get("interval") or {}).get("level"),
                    "edible_percent": edible_pct, "edible_kg": edible,
                    "donate_kg": outcome["donate_kg"],
                    "input_warnings": ml.input_warnings(bundle, inputs),
                    "model_name": bundle["model_name"],
                    "suggested_ngo": ngo["name"] if donate else None,
                    "ngo_capacity": ngo["capacity"] if donate else None,
                    "hours_to_expiry": hours})


@app.route("/donations")
def donations():
    query = request.args.get("q", "")
    status = request.args.get("status", "")
    return render_template("donations.html", donations=list_donations(query=query, status=status),
                           q=query.strip()[:80], status=status if status in config.ALL_STATUSES else "",
                           statuses=config.ALL_STATUSES, total=gather_stats()["donations"])


@app.route("/donate", methods=["GET", "POST"])
@operator_only
def donate():
    if request.method == "POST":
        form = request.form
        clean, error = validate_donation(form)
        if error:
            flash(error, "danger")
            return render_template("donate.html", ngos=get_ngos(), food_types=config.FOOD_TYPES, form=form), 400
        code = "DON-" + uuid.uuid4().hex[:8].upper()
        donation = {**clean, "donation_code": code, "status": "Pending"}
        stamp = now_iso()
        with get_db() as c:
            c.execute("""INSERT INTO donations (donation_code, donor, food_type, quantity, expiry, ngo,
                         status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                      (code, donation["donor"], donation["food_type"], donation["quantity"],
                       donation["expiry"], donation["ngo"], "Pending", stamp, stamp))
        try:
            block = chain.add_block(block_payload(donation, "DONATION_CREATED"))
        except Exception:
            log.exception("Could not add block; rolling back donation %s", code)
            with get_db() as c:
                c.execute("DELETE FROM donations WHERE donation_code=?", (code,))
            flash("Could not record the donation on the blockchain. Nothing was saved.", "danger")
            return redirect(url_for("donate"))
        with get_db() as c:
            c.execute("INSERT INTO donation_events (donation_code, status, note, created_at, block_index)"
                      " VALUES (?,?,?,?,?)", (code, "Pending", "", block.timestamp, block.index))
        flash(f"Donation {code} saved and recorded on the blockchain.", "success")
        return redirect(url_for("donation_detail", code=code))

    form = {"quantity_kg": request.args.get("qty", ""), "food_type": request.args.get("food", "")}
    return render_template("donate.html", ngos=get_ngos(), food_types=config.FOOD_TYPES, form=form)


def _detail_context(donation):
    events = get_events(donation["donation_code"])
    return {"donation": donation, "events": events,
            "steps": tracking.build_timeline(donation["status"], events),
            "next_statuses": tracking.allowed_next(donation["status"])}


@app.route("/donation/<code>")
def donation_detail(code):
    """Operator view (shows the donor and the action buttons)."""
    donation = get_donation(code)
    if donation is None:
        abort(404)
    _, _, hint = public_base()
    return render_template("donation.html", **_detail_context(donation), verification=verify_donation(code),
                           qr_available=QR_AVAILABLE, track_link=track_url(code), qr_hint=hint,
                           flow=config.STATUS_FLOW)


@app.route("/donation/<code>/status", methods=["POST"])
@operator_only
def update_status(code):
    if get_donation(code) is None:
        abort(404)
    data = request.get_json(silent=True) or request.form
    target = data.get("to")
    if not target:  # older clients: advance one step on the normal path
        target = config.STATUS_FLOW.get((get_donation(code) or {}).get("status", ""))
    try:
        if not target:
            raise TransitionError("This donation is already finished.", 409)
        updated = change_status(code, target, data.get("note", ""), data.get("collected_kg"),
                                data.get("delivered_kg"))
    except TransitionError as exc:
        if request.is_json or request.args.get("format") == "json":
            return jsonify(error=str(exc)), exc.http_status
        if exc.http_status == 404:
            abort(404)
        flash(str(exc), "warning" if exc.http_status == 409 else "danger")
        return redirect(request.referrer or url_for("donation_detail", code=code))
    if request.is_json or request.args.get("format") == "json":
        return jsonify(donation_code=code, status=updated["status"])
    flash(f"Status updated to {config.STATUS_INFO[updated['status']]['label']} (new block added to the chain).",
          "success")
    return redirect(request.referrer or url_for("donation_detail", code=code))


@app.route("/track", methods=["GET"])
def track():
    code = (request.args.get("code") or "").strip().upper()
    if code:
        if not DONATION_CODE.match(code):
            return render_template("track.html", code=code, recent=[],
                                   error="That reference does not look right. It should look like DON-1A2B3C4D."), 400
        if get_donation(code) is None:
            return render_template("track.html", code=code, recent=[],
                                   error="No donation with that reference was found."), 404
        return redirect(url_for("track_detail", code=code))
    return render_template("track.html", code="", error=None, recent=list_donations(5))


@app.route("/track/<code>")
def track_detail(code):
    """Public tracking page (also the target of the QR code). Shows no donor name and no actions."""
    donation = get_donation(code)
    if donation is None:
        return render_template("track.html", code=code, recent=[],
                               error="No donation with that reference was found."), 404
    ctx = _detail_context(donation)
    ctx["donation"] = {k: v for k, v in donation.items() if k != "donor"}
    return render_template("track_detail.html", **ctx, verification=verify_donation(code))


@app.route("/qr/<code>.png")
def qr_image(code):
    if not QR_AVAILABLE or get_donation(code) is None:
        abort(404)
    buf = io.BytesIO()
    qrcode.make(track_url(code), box_size=8, border=4).save(buf, format="PNG")
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ============================ blockchain ============================
@app.route("/chain")
def chain_view():
    """Trust & Records."""
    verdict = chain.verify()
    return render_template("chain.html", blocks=chain.get_chain(), verdict=verdict,
                           checks={b["index"]: b for b in verdict["blocks"]})


@app.route("/verify")
def verify():
    """Verify Donation: check one reference (?code=DON-...) and the whole chain."""
    verdict = chain.verify()
    code = (request.args.get("code") or "").strip().upper()
    result, error = None, None
    if code:
        if not DONATION_CODE.match(code):
            error = "That reference does not look right. It should look like DON-1A2B3C4D."
        else:
            result = verify_donation(code)
    status_code = 400 if error else 200
    if request.args.get("format") == "json":
        body = dict(verdict)
        if error:
            body["error"] = error
        if result is not None:
            body["donation"] = {k: v for k, v in result.items() if k != "blocks"}
        return jsonify(body), status_code
    return render_template("verify.html", verdict=verdict, code=code, result=result, error=error), status_code


@app.route("/tamper", methods=["POST"])
@operator_only
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
@operator_only
def restore():
    count = chain.restore()
    flash(f"Restored {count} block(s) to their original data." if count
          else "Nothing to restore: no demo tampering is active.", "info")
    return redirect(url_for("chain_view"))


@app.route("/reset", methods=["POST"])
@operator_only
def reset():
    reset_demo()
    flash("Demo data cleared. A fresh blockchain was created.", "info")
    return redirect(url_for("chain_view"))


# ============================ model, status & errors ============================
@app.route("/model")
def model_info():
    """Advanced: how the prediction model was built and how well it performs."""
    return render_template("model.html", bundle=bundle, days=config.DAYS)


def health():
    try:
        with get_db() as c:
            c.execute("SELECT 1").fetchone()
        db_ok = True
    except sqlite3.Error:
        db_ok = False
    verdict = chain.verify() if db_ok else {"valid": False, "message": "Database unavailable"}
    return {
        "status": "ok" if (db_ok and bundle is not None) else "degraded",
        "model_available": bundle is not None,
        "model_name": bundle["model_name"] if bundle else None,
        "database_available": db_ok,
        "donations": gather_stats()["donations"] if db_ok else 0,
        "blockchain_blocks": len(chain.get_chain()) if db_ok else 0,
        "blockchain_valid": verdict["valid"],
        "blockchain_message": verdict["message"],
        "qr_available": QR_AVAILABLE,
    }


@app.route("/status")
def status():
    """System health. A page for browsers; JSON for programs (or ?format=json)."""
    data = health()
    if request.args.get("format") == "json" or not wants_html():
        return jsonify(data)
    return render_template("status.html", h=data)


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403,
                           message="This action is only available on the computer that runs the app."), 403


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
    app.run(host=config.BIND_HOST, port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
