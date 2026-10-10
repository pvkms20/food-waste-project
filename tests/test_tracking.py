"""Donation lifecycle: allowed / forbidden transitions, quantities, idempotency, timeline, migration."""
import sqlite3
import threading

import pytest

import config
import tracking
from test_app import create_donation

PATH = ["Accepted", "Picked Up", "In Transit", "Delivered"]


def status_of(appmod, code):
    return appmod.get_donation(code)["status"]


def post(client, code, to, **extra):
    return client.post(f"/donation/{code}/status?format=json", json={"to": to, **extra})


# ---------------- pure rules ----------------
def test_every_status_has_info_and_rules():
    assert set(config.ALL_STATUSES) == set(config.STATUS_TRANSITIONS) == set(config.STATUS_INFO)
    assert set(config.TERMINAL_STATUSES) == {"Delivered", "Cancelled", "Rejected", "Failed"}


@pytest.mark.parametrize("a,b", [(a, b) for a in config.ALL_STATUSES for b in config.ALL_STATUSES])
def test_transition_matrix(a, b):
    assert tracking.can_transition(a, b) == (b in config.STATUS_TRANSITIONS[a])


@pytest.mark.parametrize("status,states", [
    ("Pending", ["current", "upcoming", "upcoming", "upcoming", "upcoming"]),
    ("In Transit", ["done", "done", "done", "current", "upcoming"]),
    ("Delivered", ["done"] * 5),
])
def test_timeline_states(status, states):
    reached = config.HAPPY_PATH[:config.HAPPY_PATH.index(status) + 1]
    events = [{"status": s, "created_at": "2026-01-01T10:00:00", "note": ""} for s in reached]
    steps = tracking.build_timeline(status, events)
    assert [s["state"] for s in steps] == states
    assert all(s["time"] for s in steps if s["state"] in ("done", "current"))
    assert all(s["time"] is None for s in steps if s["state"] == "upcoming")


def test_timeline_for_exceptional_status_shows_reached_steps_then_the_exception():
    events = [{"status": s, "created_at": "t", "note": ""} for s in ("Pending", "Accepted", "Picked Up", "Failed")]
    steps = tracking.build_timeline("Failed", events)
    assert [s["status"] for s in steps] == ["Pending", "Accepted", "Picked Up", "Failed"]
    assert [s["state"] for s in steps] == ["done", "done", "done", "exception"]


# ---------------- through the app ----------------
def test_full_happy_path_creates_matching_events_and_blocks(client, appmod):
    code = create_donation(client)
    for to in PATH:
        assert post(client, code, to).status_code == 200
    events = appmod.get_events(code)
    blocks = appmod.chain.donation_history(code)
    assert [e["status"] for e in events] == ["Pending"] + PATH == [b.data["status"] for b in blocks]
    assert [e["block_index"] for e in events] == [b.index for b in blocks]
    assert appmod.chain.is_valid()
    assert appmod.verify_donation(code)["state"] == "verified"


@pytest.mark.parametrize("path,bad", [
    ([], "Delivered"), ([], "In Transit"), ([], "Picked Up"), (["Accepted"], "Delivered"),
    (["Accepted", "Picked Up"], "Delivered"), (["Accepted"], "Pending"),
    (["Accepted", "Picked Up", "In Transit"], "Cancelled"),
])
def test_invalid_jumps_are_rejected_without_side_effects(client, appmod, path, bad):
    code = create_donation(client)
    for to in path:
        post(client, code, to)
    blocks = len(appmod.chain.get_chain())
    current = status_of(appmod, code)
    resp = post(client, code, bad)
    assert resp.status_code == 409 and "cannot become" in resp.get_json()["error"]
    assert status_of(appmod, code) == current and len(appmod.chain.get_chain()) == blocks


def test_repeating_the_same_transition_is_idempotent(client, appmod):
    code = create_donation(client)
    assert post(client, code, "Accepted").status_code == 200
    blocks = len(appmod.chain.get_chain())
    resp = post(client, code, "Accepted")
    assert resp.status_code == 409 and "already" in resp.get_json()["error"]
    assert len(appmod.chain.get_chain()) == blocks and len(appmod.get_events(code)) == 2


def test_concurrent_identical_requests_apply_once(client, appmod):
    code = create_donation(client)
    results = []

    def go():
        results.append(appmod.app.test_client().post(f"/donation/{code}/status?format=json",
                                                     json={"to": "Accepted"}).status_code)
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200] + [409] * 5
    assert [e["status"] for e in appmod.get_events(code)] == ["Pending", "Accepted"]
    assert appmod.chain.is_valid()


@pytest.mark.parametrize("path,final", [([], "Cancelled"), ([], "Rejected"), (["Accepted"], "Cancelled"),
                                        (["Accepted", "Picked Up"], "Failed"),
                                        (["Accepted", "Picked Up", "In Transit"], "Failed")])
def test_exceptional_endings_are_terminal(client, appmod, path, final):
    code = create_donation(client)
    for to in path:
        post(client, code, to)
    assert post(client, code, final, note="reason here").status_code == 200
    for to in config.ALL_STATUSES:
        assert post(client, code, to).status_code == 409
    assert appmod.get_events(code)[-1]["note"] == "reason here"
    assert appmod.chain.donation_history(code)[-1].data["note"] == "reason here"
    detail = client.get(f"/donation/{code}").get_data(as_text=True)
    assert "No further changes are possible" in detail


def test_unknown_status_and_missing_donation(client):
    code = create_donation(client)
    assert post(client, code, "Teleported").status_code == 400
    assert post(client, "DON-00000000", "Accepted").status_code == 404


def test_note_length_is_limited(client, appmod):
    code = create_donation(client)
    assert post(client, code, "Accepted", note="x" * 201).status_code == 400
    assert status_of(appmod, code) == "Pending"


def test_quantities_collected_and_delivered(client, appmod):
    code = create_donation(client, quantity_kg="20")
    post(client, code, "Accepted")
    assert post(client, code, "Picked Up", collected_kg="25").status_code == 400        # more than offered
    assert post(client, code, "Picked Up", collected_kg="-1").status_code == 400
    assert post(client, code, "Picked Up", collected_kg="abc").status_code == 400
    assert post(client, code, "Picked Up", collected_kg="18").status_code == 200
    post(client, code, "In Transit")
    assert post(client, code, "Delivered", delivered_kg="19").status_code == 400         # more than collected
    assert post(client, code, "Delivered", delivered_kg="17.5").status_code == 200
    d = appmod.get_donation(code)
    assert (d["quantity"], d["collected_kg"], d["delivered_kg"]) == (20, 18, 17.5)
    stats = appmod.gather_stats()
    assert stats["donated_kg"] == 20 and stats["delivered_kg"] == 17.5      # delivered uses what really arrived


def test_quantity_defaults_to_previous_stage(client, appmod):
    code = create_donation(client, quantity_kg="20")
    for to in PATH:
        post(client, code, to)
    d = appmod.get_donation(code)
    assert d["collected_kg"] == 20 and d["delivered_kg"] == 20


def test_quantity_fields_rejected_at_wrong_stage(client):
    code = create_donation(client)
    assert post(client, code, "Accepted", collected_kg="5").status_code == 400
    assert post(client, code, "Accepted", delivered_kg="5").status_code == 400


def test_failed_chain_write_reverts_the_status(client, appmod, monkeypatch):
    code = create_donation(client)
    monkeypatch.setattr(appmod.chain, "add_block", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("disk")))
    assert post(client, code, "Accepted").status_code == 500
    assert status_of(appmod, code) == "Pending" and len(appmod.get_events(code)) == 1


def test_form_post_shows_flash_message_for_invalid_step(client):
    code = create_donation(client)
    resp = client.post(f"/donation/{code}/status", data={"to": "Delivered"}, follow_redirects=True)
    assert resp.status_code == 200 and "cannot become" in resp.get_data(as_text=True)


def test_timeline_page_shows_real_timestamps_and_states(client, appmod):
    code = create_donation(client)
    post(client, code, "Accepted", note="Driver: Ravi")
    html = client.get(f"/track/{code}").get_data(as_text=True)
    assert "Accepted by NGO" in html and "Driver: Ravi" in html and "Current step" in html
    assert "Not reached yet" in html            # upcoming steps carry no invented time
    created = appmod.get_events(code)[0]["created_at"].replace("T", " ")
    assert created in html


def test_overview_numbers_come_from_records(client, appmod):
    a = create_donation(client, quantity_kg="10")
    b = create_donation(client, quantity_kg="5")
    c = create_donation(client, quantity_kg="7")
    for to in PATH:
        post(client, a, to)
    post(client, b, "Accepted"); post(client, b, "Picked Up")
    post(client, c, "Rejected")
    s = appmod.gather_stats()
    assert (s["donations"], s["delivered"], s["in_transit"], s["awaiting_pickup"], s["attention"]) == (3, 1, 1, 0, 1)
    assert s["donated_kg"] == 22 and s["delivered_kg"] == 10
    html = client.get("/overview").get_data(as_text=True)
    assert "Needs attention" in html and c in html


def test_empty_overview_has_an_empty_state(client):
    assert "No donations yet" in client.get("/overview").get_data(as_text=True)


def test_expired_active_donation_needs_attention(client, appmod):
    code = create_donation(client)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE donations SET expiry='2020-01-01 10:00' WHERE donation_code=?", (code,))
    conn.commit(); conn.close()
    assert appmod.get_donation(code)["needs_attention"] is True
    assert appmod.gather_stats()["attention"] == 1


def test_legacy_database_is_migrated(appmod):
    """An old database (3-state flow, no events table / new columns) keeps working."""
    code = "DON-ABCDEF12"
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript("DROP TABLE donation_events; DROP TABLE donations;")
    conn.execute("""CREATE TABLE donations (id INTEGER PRIMARY KEY AUTOINCREMENT, donation_code TEXT NOT NULL UNIQUE,
        donor TEXT NOT NULL, food_type TEXT NOT NULL, quantity REAL NOT NULL, expiry TEXT NOT NULL, ngo TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO donations (donation_code, donor, food_type, quantity, expiry, ngo, status, created_at)"
                 " VALUES (?,?,?,?,?,?,?,?)", (code, "Old", "Cooked rice", 12, "2099-01-01 10:00", "Hope Meals Trust",
                                               "Picked Up", "2026-01-01T09:00:00"))
    conn.commit(); conn.close()
    for st in ("Pending", "Picked Up"):
        appmod.chain.add_block({"event": "X", "donation_code": code, "donor_ref": "r", "food_type": "Cooked rice",
                                "quantity_kg": 12, "ngo": "Hope Meals Trust", "status": st, "expiry": "2099-01-01 10:00"})
    appmod.init_db()
    d = appmod.get_donation(code)
    assert d["status"] == "Picked Up" and d["updated_at"] == "2026-01-01T09:00:00"
    assert [e["status"] for e in appmod.get_events(code)] == ["Pending", "Picked Up"]
    c = appmod.app.test_client()
    assert c.get(f"/track/{code}").status_code == 200 and c.get(f"/donation/{code}").status_code == 200
    assert c.post(f"/donation/{code}/status?format=json", json={"to": "In Transit"}).status_code == 200
