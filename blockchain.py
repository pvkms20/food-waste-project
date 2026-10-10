"""Educational blockchain for donation traceability, persisted in SQLite.

This is a SIMULATION for tamper detection. It is a single-node, private hash chain:
there is no network, consensus or mining, and it is not comparable to Ethereum or Bitcoin.

Each block stores: index, timestamp, data, previous_hash, hash (SHA-256).
The hash covers all four other fields, so changing ANY stored value breaks the block's hash,
and changing a block's hash would break the next block's previous_hash link.
"""
import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime

log = logging.getLogger(__name__)

GENESIS_PREVIOUS_HASH = "0"


def calculate_hash(index, timestamp, data, previous_hash):
    """Deterministic SHA-256: same inputs always give the same hash (keys sorted, fixed separators)."""
    payload = json.dumps(
        {"index": index, "timestamp": timestamp, "data": data, "previous_hash": previous_hash},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Block:
    index: int
    timestamp: str
    data: dict
    previous_hash: str
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self.compute_hash()

    def compute_hash(self):
        return calculate_hash(self.index, self.timestamp, self.data, self.previous_hash)

    def to_dict(self):
        return asdict(self)


class Blockchain:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_tables()
        if not self.get_chain():
            self._create_genesis()

    # ---------------- storage ----------------
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)  # wait for locks instead of failing
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        with self._conn() as c:
            # "index" is quoted because INDEX is an SQL keyword
            c.execute("""CREATE TABLE IF NOT EXISTS blocks (
                "index" INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, data TEXT NOT NULL,
                prev_hash TEXT NOT NULL, hash TEXT NOT NULL)""")
            # Used only by the demo tamper test so the original data can be restored.
            c.execute("""CREATE TABLE IF NOT EXISTS tamper_backup (
                block_index INTEGER PRIMARY KEY, data TEXT NOT NULL)""")

    def _save(self, block):
        with self._conn() as c:
            c.execute('INSERT INTO blocks ("index", timestamp, data, prev_hash, hash) VALUES (?,?,?,?,?)',
                      (block.index, block.timestamp, json.dumps(block.data, sort_keys=True),
                       block.previous_hash, block.hash))

    def _create_genesis(self):
        genesis = Block(0, _now(), {"event": "GENESIS", "note": "Donation traceability chain started"},
                        GENESIS_PREVIOUS_HASH)
        self._save(genesis)

    # ---------------- chain operations ----------------
    def get_chain(self):
        with self._conn() as c:
            rows = c.execute('SELECT * FROM blocks ORDER BY "index"').fetchall()
        chain = []
        for r in rows:
            try:
                data = json.loads(r["data"])
                if not isinstance(data, dict):
                    raise ValueError("block data is not an object")
            except (ValueError, TypeError):
                # Corrupted data: keep the block visible; its hash will fail verification.
                log.warning("Block %s has unreadable data", r["index"])
                data = {"_corrupted": str(r["data"])[:200]}
            chain.append(Block(r["index"], r["timestamp"], data, r["prev_hash"], r["hash"]))
        return chain

    def add_block(self, data):
        """Appends a block linked to the current last block and returns it.

        Reading the last block and inserting the new one happen inside ONE write transaction
        (BEGIN IMMEDIATE), so two simultaneous requests can never both link to the same parent.
        """
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute('SELECT "index", hash FROM blocks ORDER BY "index" DESC LIMIT 1').fetchone()
                if row is None:  # empty table: start the chain first
                    genesis = Block(0, _now(), {"event": "GENESIS", "note": "Donation traceability chain started"},
                                    GENESIS_PREVIOUS_HASH)
                    self._insert(conn, genesis)
                    last_index, last_hash = genesis.index, genesis.hash
                else:
                    last_index, last_hash = row["index"], row["hash"]
                block = Block(last_index + 1, _now(), data, last_hash)
                self._insert(conn, block)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return block

    @staticmethod
    def _insert(conn, block):
        conn.execute('INSERT INTO blocks ("index", timestamp, data, prev_hash, hash) VALUES (?,?,?,?,?)',
                     (block.index, block.timestamp, json.dumps(block.data, sort_keys=True),
                      block.previous_hash, block.hash))

    def verify(self):
        """Recomputes every hash and checks every link.

        Returns {valid, message, bad_index, blocks:[{index, hash_ok, link_ok}]}.
        """
        chain = self.get_chain()
        if not chain:
            return {"valid": False, "message": "The chain is empty (no genesis block).",
                    "bad_index": None, "blocks": []}
        details, first_problem = [], None
        for pos, block in enumerate(chain):
            hash_ok = block.compute_hash() == block.hash
            if pos == 0:
                link_ok = block.index == 0 and block.previous_hash == GENESIS_PREVIOUS_HASH
            else:
                prev = chain[pos - 1]
                link_ok = block.index == prev.index + 1 and block.previous_hash == prev.hash
            details.append({"index": block.index, "hash_ok": hash_ok, "link_ok": link_ok})
            if first_problem is None and not (hash_ok and link_ok):
                first_problem = (block.index, hash_ok)
        if first_problem is None:
            return {"valid": True, "bad_index": None, "blocks": details,
                    "message": f"All {len(chain)} blocks verified: every hash matches and every link is intact."}
        index, hash_ok = first_problem
        if not hash_ok:
            msg = (f"Block {index} was modified: its stored hash no longer matches its contents "
                   f"(data, timestamp or links changed).")
        else:
            msg = f"Block {index} is not correctly linked to the previous block (previous_hash mismatch)."
        return {"valid": False, "bad_index": index, "blocks": details, "message": msg}

    def is_valid(self):
        return self.verify()["valid"]

    # ---------------- donation helpers ----------------
    def donation_history(self, donation_code):
        return [b for b in self.get_chain() if b.data.get("donation_code") == donation_code]

    # ---------------- demo helpers ----------------
    def tamper(self, index=None):
        """DEMO ONLY. Edits a stored block's data directly in the database WITHOUT recomputing its hash,
        which is exactly what a malicious edit would look like. Returns the tampered index or None."""
        chain = self.get_chain()
        indexes = {b.index for b in chain}
        target = chain[-1].index if index is None else index
        if target == 0 or target not in indexes:
            return None
        with self._conn() as c:
            row = c.execute('SELECT data FROM blocks WHERE "index"=?', (target,)).fetchone()
            c.execute("INSERT OR IGNORE INTO tamper_backup (block_index, data) VALUES (?,?)",
                      (target, row["data"]))
            try:
                data = json.loads(row["data"])
                if "quantity_kg" in data:
                    data["quantity_kg"] = round(float(data["quantity_kg"]) + 100, 1)
                else:
                    data["tampered"] = True
            except (ValueError, TypeError):
                data = {"tampered": True}
            c.execute('UPDATE blocks SET data=? WHERE "index"=?', (json.dumps(data, sort_keys=True), target))
        return target

    def restore(self):
        """Undoes the demo tamper test by putting the original data back. Returns blocks restored."""
        with self._conn() as c:
            rows = c.execute("SELECT block_index, data FROM tamper_backup").fetchall()
            for r in rows:
                c.execute('UPDATE blocks SET data=? WHERE "index"=?', (r["data"], r["block_index"]))
            c.execute("DELETE FROM tamper_backup")
        return len(rows)

    def reset(self):
        """Deletes every block and starts a fresh chain."""
        with self._conn() as c:
            c.execute("DELETE FROM blocks")
            c.execute("DELETE FROM tamper_backup")
        self._create_genesis()


def _now():
    return datetime.now().isoformat(timespec="seconds")
