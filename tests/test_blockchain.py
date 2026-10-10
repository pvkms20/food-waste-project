import json
import os
import sqlite3
import tempfile

import pytest

from blockchain import Block, Blockchain, calculate_hash


@pytest.fixture()
def chain():
    return Blockchain(os.path.join(tempfile.mkdtemp(), "chain.db"))


def donation(code="DON-AAAAAAAA", status="Pending", qty=10.0):
    return {"donation_code": code, "status": status, "quantity_kg": qty, "ngo": "Test NGO"}


def test_genesis_block_exists(chain):
    blocks = chain.get_chain()
    assert len(blocks) == 1
    assert blocks[0].index == 0
    assert blocks[0].previous_hash == "0"
    assert blocks[0].data["event"] == "GENESIS"


def test_add_block_links_to_previous(chain):
    b1 = chain.add_block(donation())
    b2 = chain.add_block(donation(status="Picked Up"))
    assert (b1.index, b2.index) == (1, 2)
    assert b1.previous_hash == chain.get_chain()[0].hash
    assert b2.previous_hash == b1.hash


def test_hash_is_sha256_and_deterministic():
    h1 = calculate_hash(1, "2026-01-01T00:00:00", {"a": 1, "b": 2}, "0")
    h2 = calculate_hash(1, "2026-01-01T00:00:00", {"b": 2, "a": 1}, "0")  # key order must not matter
    assert h1 == h2
    assert len(h1) == 64 and int(h1, 16) >= 0
    assert h1 != calculate_hash(1, "2026-01-01T00:00:00", {"a": 1, "b": 3}, "0")


def test_block_hash_matches_its_contents():
    block = Block(3, "2026-01-01T00:00:00", {"x": 1}, "abc")
    assert block.hash == block.compute_hash()
    block.data["x"] = 2
    assert block.hash != block.compute_hash()


def test_valid_chain(chain):
    chain.add_block(donation())
    chain.add_block(donation(status="Delivered"))
    verdict = chain.verify()
    assert chain.is_valid() is True
    assert verdict["valid"] and verdict["bad_index"] is None
    assert all(b["hash_ok"] and b["link_ok"] for b in verdict["blocks"])


def test_modified_data_is_detected(chain):
    chain.add_block(donation())
    chain.add_block(donation(code="DON-BBBBBBBB"))
    assert chain.tamper(1) == 1
    verdict = chain.verify()
    assert verdict["valid"] is False and verdict["bad_index"] == 1
    assert "modified" in verdict["message"]


def test_broken_link_is_detected(chain):
    chain.add_block(donation())
    b2 = chain.add_block(donation(code="DON-BBBBBBBB"))
    # Rewrite block 2 consistently (valid own hash) but with a wrong previous_hash
    forged = Block(b2.index, b2.timestamp, b2.data, "f" * 64)
    with sqlite3.connect(chain.db_path) as c:
        c.execute('UPDATE blocks SET prev_hash=?, hash=? WHERE "index"=?', (forged.previous_hash, forged.hash, 2))
    verdict = chain.verify()
    assert verdict["valid"] is False and verdict["bad_index"] == 2
    assert "linked" in verdict["message"]


def test_restore_undoes_tamper(chain):
    chain.add_block(donation(qty=10.0))
    chain.tamper(1)
    assert not chain.is_valid()
    assert chain.restore() == 1
    assert chain.is_valid()
    assert chain.get_chain()[1].data["quantity_kg"] == 10.0


def test_cannot_tamper_genesis_or_missing_block(chain):
    chain.add_block(donation())
    assert chain.tamper(0) is None
    assert chain.tamper(99) is None
    assert chain.is_valid()


def test_corrupted_block_data_is_reported_not_crashing(chain):
    chain.add_block(donation())
    with sqlite3.connect(chain.db_path) as c:
        c.execute('UPDATE blocks SET data=? WHERE "index"=1', ("{not json",))
    verdict = chain.verify()
    assert verdict["valid"] is False and verdict["bad_index"] == 1


def test_deleted_block_breaks_chain(chain):
    for i in range(3):
        chain.add_block(donation(code=f"DON-0000000{i}"))
    with sqlite3.connect(chain.db_path) as c:
        c.execute('DELETE FROM blocks WHERE "index"=2')
    verdict = chain.verify()
    assert verdict["valid"] is False and verdict["bad_index"] == 3


def test_empty_chain_is_invalid_and_reinitialises(chain):
    with sqlite3.connect(chain.db_path) as c:
        c.execute("DELETE FROM blocks")
    assert chain.verify()["valid"] is False
    assert Blockchain(chain.db_path).is_valid()  # new instance recreates genesis


def test_donation_history_filters_by_code(chain):
    chain.add_block(donation(code="DON-AAAAAAAA", status="Pending"))
    chain.add_block(donation(code="DON-BBBBBBBB", status="Pending"))
    chain.add_block(donation(code="DON-AAAAAAAA", status="Delivered"))
    history = chain.donation_history("DON-AAAAAAAA")
    assert [b.data["status"] for b in history] == ["Pending", "Delivered"]


def test_reset_gives_single_valid_block(chain):
    chain.add_block(donation())
    chain.reset()
    assert len(chain.get_chain()) == 1 and chain.is_valid()


def test_data_roundtrips_through_database_with_same_hash(chain):
    block = chain.add_block({"donation_code": "DON-AAAAAAAA", "quantity_kg": 12.5, "nested": {"z": 1, "a": [1, 2]}})
    stored = chain.get_chain()[1]
    assert stored.hash == block.hash == stored.compute_hash()
    assert json.loads(json.dumps(stored.data)) == block.data


def test_concurrent_add_block_keeps_chain_valid(chain):
    """Simultaneous requests must never fork the chain or crash on a duplicate index."""
    import threading
    errors = []

    def worker(n):
        try:
            for i in range(3):
                chain.add_block(donation(code=f"DON-{n:04d}{i:04d}"))
        except Exception as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    blocks = chain.get_chain()
    assert errors == []
    assert [b.index for b in blocks] == list(range(31))
    assert chain.is_valid()


def test_add_block_on_empty_table_creates_genesis_first(chain):
    with sqlite3.connect(chain.db_path) as c:
        c.execute("DELETE FROM blocks")
    block = chain.add_block(donation())
    assert block.index == 1 and len(chain.get_chain()) == 2 and chain.is_valid()
