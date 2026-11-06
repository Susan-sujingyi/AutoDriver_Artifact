"""Durable, host-only request reservations. Unknown outcomes keep reservations."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING
from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


class BudgetDenied(RuntimeError):
    pass


def micro_cny(value) -> int:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError()
        return int((number * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Expected a finite, non-negative RMB amount") from None


def load_pricing(path: Path, model: str, provider_url: str) -> dict:
    pricing = json.loads(path.read_text())
    if (pricing.get("currency") != "CNY" or pricing.get("model") != model
            or pricing.get("provider_url", "").rstrip("/") != provider_url.rstrip("/")
            or pricing.get("authority") != "user_provided"
            or not pricing.get("source") or not pricing.get("checked_at")):
        raise BudgetDenied("Pricing must be explicitly supplied for this exact provider/model")
    for key in ("max_input_cny_per_million", "max_output_cny_per_million", "max_other_cny_per_request"):
        if pricing.get(key) is None:
            raise BudgetDenied("Missing conservative price bound: " + key)
        micro_cny(pricing[key])
    if pricing.get("output_limit_includes_reasoning") is not True:
        raise BudgetDenied("Provider output-token limit must include billable reasoning tokens")
    return pricing


def request_bound(pricing: dict, input_tokens: int, output_tokens: int) -> int:
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token bounds must be non-negative")
    # Rate units are CNY / million tokens; result is micro-CNY.
    cost = (Decimal(str(pricing["max_input_cny_per_million"])) * input_tokens
            + Decimal(str(pricing["max_output_cny_per_million"])) * output_tokens
            + micro_cny(pricing["max_other_cny_per_request"]))
    return int(cost.to_integral_value(rounding=ROUND_CEILING))


class Ledger:
    @classmethod
    def for_run(cls, path: Path):
        """Reopen the established cap; never reset a shared experiment budget."""
        path = Path(path)
        if not path.exists():
            return cls(path)
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            cap = db.execute("SELECT cap FROM limits WHERE id=1").fetchone()[0]
        return cls(path, None if cap == -1 else Decimal(cap) / 1_000_000)

    def __init__(self, path: Path, stage_limit_cny=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        new_database = not path.exists()
        self.path = path
        with self.connect() as db:
            # New execution revisions use new batch ledgers. Never silently
            # migrate or checkpoint a historical ledger merely by reopening it.
            if new_database and db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                raise RuntimeError('New ledger requires SQLite WAL support')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS limits (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY, episode TEXT NOT NULL, bound INTEGER NOT NULL,
                  charged INTEGER, status TEXT NOT NULL, created REAL NOT NULL, usage TEXT);
                CREATE TABLE IF NOT EXISTS reconciliations (
                  request_id TEXT PRIMARY KEY, previous_status TEXT NOT NULL,
                  previous_bound INTEGER NOT NULL, amount INTEGER NOT NULL,
                  evidence_sha256 TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL);
            """)
            limit = -1 if stage_limit_cny is None else micro_cny(stage_limit_cny)
            db.execute("INSERT OR IGNORE INTO limits VALUES (1, ?)", (limit,))
            if db.execute("SELECT cap FROM limits").fetchone()[0] != limit:
                raise BudgetDenied("Cannot silently change an existing ledger's stage limit")
        path.chmod(0o600)

    def set_limit(self, amount):
        """Explicit campaign-cap change; never reset requests or reservations."""
        limit = -1 if amount is None else micro_cny(amount)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS limit_changes (changed REAL, previous INTEGER, current INTEGER)")
            previous = db.execute("SELECT cap FROM limits WHERE id=1").fetchone()[0]
            if limit != previous:
                db.execute("INSERT INTO limit_changes VALUES (?,?,?)", (time.time(), previous, limit))
                db.execute("UPDATE limits SET cap=? WHERE id=1", (limit,))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, episode: str, bound: int, episode_limit_cny, max_requests: int, *, guard=None) -> str:
        if bound < 0 or max_requests < 1:
            raise ValueError("Invalid reservation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM requests WHERE status='bound_violation' LIMIT 1").fetchone():
                raise BudgetDenied("A provider bound violation requires host review; no further calls allowed")
            stage = db.execute("SELECT COALESCE(SUM(COALESCE(charged,bound)),0) FROM requests").fetchone()[0]
            used, count = db.execute("SELECT COALESCE(SUM(COALESCE(charged,bound)),0), COUNT(*) FROM requests WHERE episode=?", (episode,)).fetchone()
            if count >= max_requests:
                raise BudgetDenied("Request-count limit reached")
            cap = db.execute("SELECT cap FROM limits").fetchone()[0]
            if cap != -1 and stage + bound > cap:
                raise BudgetDenied("Insufficient stage budget for the next request reservation")
            if used + bound > micro_cny(episode_limit_cny):
                raise BudgetDenied("Insufficient episode budget for the next request reservation")
            ident = uuid.uuid4().hex
            if guard is not None:guard(db,ident)
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)", (ident, episode, bound, None, "reserved", time.time(), None))
            return ident

    def finish(self, ident: str, charged: int | None, usage: dict | None, status: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT bound,status FROM requests WHERE id=?", (ident,)).fetchone()
            if row is None or row[1] != "reserved":
                raise BudgetDenied("Reservation missing or already finalized")
            if charged is not None and (charged < 0 or charged > row[0]):
                # Keep the full bound, and make this unexpected provider outcome explicit.
                db.execute("UPDATE requests SET status='bound_violation',usage=? WHERE id=?", (json.dumps(usage), ident))
                db.commit()
                raise BudgetDenied("Provider usage exceeded the conservative reservation")
            db.execute("UPDATE requests SET charged=?,status=?,usage=? WHERE id=?", (charged, status, json.dumps(usage), ident))

    def summary(self) -> dict:
        with self.connect() as db:
            db.execute('BEGIN')  # One consistent read snapshot, without a write lock.
            rows = db.execute("SELECT id,episode,bound,charged,status,usage FROM requests ORDER BY created").fetchall()
            cap = db.execute("SELECT cap FROM limits").fetchone()[0]
            reconciled = {r[0]:r[1] for r in db.execute('SELECT request_id,amount FROM reconciliations')}
        return {"currency": "CNY", "stage_limit_cny": None if cap == -1 else cap / 1_000_000,
                "accounted_upper_cny": sum(r[3] if r[3] is not None else r[2] for r in rows) / 1_000_000,
                "actual_billed_cny": None,
                "user_confirmed_billed_cny": sum(reconciled.values()) / 1_000_000,
                "note": "Provider-rate estimates, retained reservations, or explicit user-reconciled charges; platform invoice not independently verified.",
                "requests": [{"id": r[0], "episode": r[1], "reserved_upper_cny": r[2] / 1_000_000,
                              "settled_upper_cny": r[3] / 1_000_000 if r[3] is not None else None,
                              "status": r[4], "usage": json.loads(r[5]) if r[5] else None} for r in rows]}

    def reconcile_user_receipt(self, ident: str, evidence: Path):
        """Append an explicit host audit, never erase the original reservation.

        Only finalized unknown outcomes may be reconciled. User confirmation
        associates this receipt with the request; it does not invent token usage
        or claim a successful response.
        """
        raw=evidence.read_bytes();value=json.loads(raw)
        if (value.get('association_status')!='confirmed_by_user'
                or value.get('associated_request_id')!=ident or value.get('source')!='user message'):
            raise BudgetDenied('Receipt/request association must be explicitly confirmed by the user')
        amount=micro_cny(value['amount_cny'])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status,bound FROM requests WHERE id=?',(ident,)).fetchone()
            if row is None or row[0] not in ('outcome_unknown','usage_unknown'):
                raise BudgetDenied('Only finalized unknown costs can be reconciled once')
            db.execute('INSERT INTO reconciliations VALUES (?,?,?,?,?,?,?)',
                (ident,row[0],row[1],amount,hashlib.sha256(raw).hexdigest(),'user-confirmed transaction',time.time()))
            status='reconciled_user_receipt' if amount<=row[1] else 'bound_violation'
            db.execute('UPDATE requests SET charged=?,status=? WHERE id=?',(amount,status,ident))
