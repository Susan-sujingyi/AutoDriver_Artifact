"""Durable host response recovery without another upstream request.

Request context is committed before contacting the provider. Receipt files
and delivery claims survive process restart. A started delivery is never
automatically repeated: a socket write cannot prove client consumption.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import time
import uuid

from .accounting import estimate
from .budget import BudgetDenied


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ResponseProtocolError(ValueError):
    """A received provider response is ambiguous; this is not budget denial."""


TRAILER_POLICY = 'completed-empty-scanner-trailer/v1'


def _scanner_trailer(response):
    """Only the observed empty proxy error, never a second model result."""
    error = response.get('error')
    return (response.get('status') == 'failed' and response.get('id') == '' and
            response.get('model') == '' and response.get('output') in (None, []) and
            response.get('usage') is None and response.get('created_at') == 0 and
            isinstance(error, dict) and error.get('type') == 'up_stream_break' and
            error.get('code') == 'up_stream_break' and
            error.get('message') == 'responses stream ended without response.completed: scanner_error' and
            error.get('param') is None)


def terminal_analysis(payload):
    terminals = []
    for line in payload.decode("utf-8").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            value = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") in (
                "response.completed", "response.incomplete", "response.failed"):
            response = value.get("response")
            if not isinstance(response, dict) or response.get("status") != value["type"].split(".", 1)[1]:
                raise ResponseProtocolError("Response terminal envelope is inconsistent")
            terminals.append(response)
        elif terminals and isinstance(value, dict):
            raise ResponseProtocolError('Response data followed a terminal event')
    if not terminals:
        raise ResponseProtocolError("Receipt has no terminal response")
    first = terminals[0]
    usage = first.get('usage')
    complete = (first.get('status') == 'completed' and isinstance(first.get('id'), str) and bool(first['id']) and
                isinstance(first.get('model'), str) and bool(first['model']) and isinstance(first.get('output'), list) and
                first.get('error') is None and isinstance(usage, dict) and
                all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')))
    trailers = 0
    duplicates = 0
    for item in terminals[1:]:
        if not trailers and encoded(item) == encoded(first):
            duplicates += 1
        elif complete and _scanner_trailer(item):
            trailers += 1
        else:
            raise ResponseProtocolError("Receipt contains conflicting terminal responses")
    return first, {'policy': TRAILER_POLICY if trailers else 'identical-terminal-dedup/v1',
                   'ignored_scanner_trailers': trailers, 'duplicate_terminals': duplicates,
                   'selected_response_id': first.get('id')}


def terminal_response(payload):
    return terminal_analysis(payload)[0]


def delivery_payload(payload):
    """Normalize whole SSE frames, preserving the original receipt verbatim."""
    terminal_response(payload)
    frames = re.split(r'(\r?\n\r?\n)', payload.decode('utf-8'))
    seen = False
    result = []
    for offset in range(0, len(frames), 2):
        frame = frames[offset]
        terminal = False
        for line in frame.splitlines():
            if not line.startswith('data: '):
                continue
            try:
                value = json.loads(line[6:])
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict) and value.get('type') in (
                    'response.completed', 'response.incomplete', 'response.failed'):
                terminal = True
        if terminal and seen:
            continue
        seen |= terminal
        result.append(frame)
        if offset + 1 < len(frames):
            result.append(frames[offset + 1])
    return ''.join(result).encode('utf-8')


class ResponseStore:
    def __init__(self, ledger, directory):
        self.ledger = ledger
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise ValueError("Response store must be a host-owned directory")
        with ledger.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS response_contexts (
                  request_id TEXT PRIMARY KEY, context TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS response_receipts (
                  request_id TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL,
                  received REAL NOT NULL, delivery TEXT NOT NULL,
                  started REAL, completed REAL, error TEXT);
                CREATE TABLE IF NOT EXISTS response_settlements (
                  request_id TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL,
                  previous_status TEXT NOT NULL, previous_charged INTEGER,
                  previous_usage TEXT, charged INTEGER, status TEXT NOT NULL,
                  repaired REAL NOT NULL);
            """)

    def prepare(self, request_id, *, episode, logical_request_id, body, pricing, provider_url):
        """Commit identity/configuration before any paid provider operation."""
        if not logical_request_id or type(body.get("max_output_tokens")) is not int or body["max_output_tokens"] < 1:
            raise ValueError("Missing logical request identity or output bound")
        context = {"episode": episode, "logical_request_id": logical_request_id,
                   "request_sha256": sha(encoded(body)), "model": body["model"],
                   "provider_url": provider_url, "max_output_tokens": body["max_output_tokens"],
                   "pricing": pricing}
        # Preserve the original key before contacting the provider. Recovery
        # never invents or edits this evidence in an existing receipt/context.
        if "prompt_cache_key" in body:
            context["prompt_cache_key"] = body["prompt_cache_key"]
        text = encoded(context).decode()
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT episode,status FROM requests WHERE id=?", (request_id,)).fetchone()
            if row != (episode, "reserved"):
                raise BudgetDenied("Response context requires this episode's live reservation")
            previous = db.execute("SELECT context FROM response_contexts WHERE request_id=?", (request_id,)).fetchone()
            if previous and previous[0] != text:
                raise BudgetDenied("Request context cannot change after reservation")
            db.execute("INSERT OR IGNORE INTO response_contexts VALUES (?,?)", (request_id, text))
        return context

    def context(self, request_id):
        with self.ledger.connect() as db:
            row = db.execute("SELECT context FROM response_contexts WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise BudgetDenied("No pre-request context; legacy receipts require explicit review")
        return json.loads(row[0])

    def match_replay(self, request_id, body, *, cache_key_policy=None, resume_prompt=None):
        """Replay only the exact original normalized request."""
        if cache_key_policy is not None or resume_prompt is not None:
            raise BudgetDenied('Request transformations are not supported')
        context, _, _, receipt_sha = self.load(request_id)
        actual = sha(encoded(body))
        if actual != context['request_sha256']:
            raise BudgetDenied('Replay request does not match its original context')
        return dict(request_sha256=actual, received_request_sha256=actual,
                    receipt_sha256=receipt_sha, cache_key_transition=None,
                    resume_control_transition=None)

    def path(self, request_id):
        # Ledger request ids are UUID hex; never interpret an agent-supplied path.
        if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise ValueError("Invalid request id")
        return self.directory / (request_id + ".json")

    def save(self, request_id, payload):
        context = self.context(request_id)
        self.save_raw(request_id, payload)
        terminal_response(payload)
        value = {"schema": "autodriver.response-receipt/v2", "request_id": request_id,
                 "logical_request_id": context["logical_request_id"],
                 "request_sha256": context["request_sha256"], "context_sha256": sha(encoded(context)),
                 "payload_sha256": sha(payload), "payload": payload.decode("utf-8")}
        raw = encoded(value)
        path = self.path(request_id)
        if path.exists():
            if path.is_symlink() or path.read_bytes() != raw:
                raise BudgetDenied("Never replace a different response receipt")
        else:
            temporary = path.with_suffix(".tmp")
            if temporary.exists():
                if temporary.is_symlink() or temporary.read_bytes() != raw:
                    raise BudgetDenied("Incomplete or conflicting receipt publication requires host review")
            else:
                with temporary.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            # Exclusive publication prevents a concurrent writer overwriting
            # the canonical receipt; a crash leaves either .tmp or a full file.
            os.link(temporary, path)
            temporary.unlink()
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        self.load(request_id)  # Bind file identity in the same durable ledger.
        # The committed receipt now owns the original bytes; retain a separate
        # .sse only for incomplete/invalid responses that cannot form a receipt.
        self.path(request_id).with_suffix('.sse').unlink(missing_ok=True)
        return value

    def save_raw(self, request_id, payload):
        """Keep sanitized wire bytes even when parsing fails. Never overwrite."""
        path = self.path(request_id).with_suffix('.sse')
        if path.exists():
            if path.is_symlink() or path.read_bytes() != payload:
                raise BudgetDenied('Never replace different raw response evidence')
        else:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return {'path': path.name, 'sha256': sha(payload), 'bytes': len(payload)}

    def load(self, request_id):
        context = self.context(request_id)
        path = self.path(request_id)
        temporary = path.with_suffix(".tmp")
        orphan = not path.exists() and temporary.is_file() and not temporary.is_symlink()
        source = temporary if orphan else path
        if source.is_symlink() or not source.is_file():
            raise BudgetDenied("Complete host response receipt is unavailable")
        raw = source.read_bytes()
        value = json.loads(raw)
        payload = value.get("payload", "").encode("utf-8")
        if (value.get("schema") != "autodriver.response-receipt/v2" or value.get("request_id") != request_id or
                value.get("context_sha256") != sha(encoded(context)) or
                value.get("request_sha256") != context["request_sha256"] or
                value.get("logical_request_id") != context["logical_request_id"] or
                value.get("payload_sha256") != sha(payload)):
            raise BudgetDenied("Response receipt/context identity mismatch")
        final = terminal_response(payload)
        if orphan:
            os.link(temporary, path)
            temporary.unlink()
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT receipt_sha256 FROM response_receipts WHERE request_id=?", (request_id,)).fetchone()
            if previous and previous[0] != sha(raw):
                raise BudgetDenied("Bound host response receipt changed")
            # Recover the file-publication/database-commit crash window using
            # the immutable pre-request context, before claiming delivery.
            db.execute("INSERT OR IGNORE INTO response_receipts VALUES (?,?,?,'ready',NULL,NULL,NULL)",
                       (request_id, sha(raw), time.time()))
        return context, value, final, sha(raw)

    def settle(self, request_id):
        """Repair the original ledger row transactionally; no new reservation."""
        context, receipt, final, receipt_sha = self.load(request_id)
        usage = final.get("usage")
        accounting = estimate(usage, context["pricing"])
        charged = accounting["estimate_upper_micro_cny"]
        invalid_usage = isinstance(usage, dict) and any(
            type(usage.get(key)) is int and not 0 <= usage[key] <= limit for key, limit in (
                ("input_tokens", context["pricing"]["max_input_tokens_per_request"]),
                ("output_tokens", context["max_output_tokens"])))
        usage_text = json.dumps(usage, sort_keys=True)
        status = "usage_recorded" if charged is not None else "usage_unknown"
        violation = False
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT episode,bound,charged,status,usage FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None or row[0] != context["episode"]:
                raise BudgetDenied("Receipt does not belong to the ledger episode")
            if row[3] in ("bound_violation", "reconciled_user_receipt"):
                raise BudgetDenied("Host-reviewed accounting cannot be replaced by receipt replay")
            previous = db.execute("SELECT receipt_sha256,charged,status FROM response_settlements WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if (previous != (receipt_sha, charged, status) or row[2:4] != (charged, status) or
                        json.loads(row[4] or "null") != usage):
                    raise BudgetDenied("Response settlement identity changed")
                return {"status": "already_settled", "request_id": request_id, "accounting": accounting}
            if row[3] in ("usage_recorded", "usage_unknown"):
                if row[2] != charged or json.loads(row[4] or "null") != usage:
                    raise BudgetDenied("Existing settled usage conflicts with response receipt")
            elif row[3] not in ("reserved", "outcome_unknown"):
                raise BudgetDenied("Unsupported request accounting state")
            if invalid_usage or (charged is not None and not 0 <= charged <= row[1]):
                charged, status, violation = None, "bound_violation", True
            db.execute("INSERT INTO response_settlements VALUES (?,?,?,?,?,?,?,?)", (
                request_id, receipt_sha, row[3], row[2], row[4], charged, status, time.time()))
            db.execute("UPDATE requests SET charged=?,status=?,usage=? WHERE id=?", (charged, status, usage_text, request_id))
        if violation:
            raise BudgetDenied("Recovered usage exceeds its original reservation or token bound")
        return {"status": "settled", "request_id": request_id, "accounting": accounting}

    def claim_delivery(self, request_id, *, episode, request_sha256, allow_failed=False):
        context, receipt, final, receipt_sha = self.load(request_id)
        if context["episode"] != episode or context["request_sha256"] != request_sha256:
            raise BudgetDenied("Replay requires the same episode and exact normalized request")
        if final["status"] not in ("completed", "incomplete") and not allow_failed:
            raise BudgetDenied("Failed provider response cannot be replayed as a generated result")
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute("SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
            row = db.execute("SELECT receipt_sha256,delivery FROM response_receipts WHERE request_id=?", (request_id,)).fetchone()
            if request is None or request[0] not in ("usage_recorded", "usage_unknown"):
                raise BudgetDenied("Response must be accounted before delivery")
            if row != (receipt_sha, "ready"):
                raise BudgetDenied("Response delivery already started; client consumption is not safe to assume")
            db.execute("UPDATE response_receipts SET delivery='started',started=? WHERE request_id=?", (time.time(), request_id))
        return receipt["payload"].encode("utf-8")

    def delivered(self, request_id):
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("UPDATE response_receipts SET delivery='server_write_completed',completed=? WHERE request_id=? AND delivery='started'",
                                 (time.time(), request_id)).rowcount
            if updated != 1:
                raise BudgetDenied("Delivery completion requires one prior claim")

    def state(self, request_id):
        self.load(request_id)
        with self.ledger.connect() as db:
            delivery = db.execute("SELECT delivery FROM response_receipts WHERE request_id=?", (request_id,)).fetchone()[0]
        return {"request_id": request_id, "delivery": delivery,
                "replayable": delivery == "ready", "client_consumption": "unknown"}


def main(argv=None):
    import argparse
    from benchmark.common.api import owned
    from .budget import Ledger
    parser = argparse.ArgumentParser(description="Inspect or settle a saved response; never call a provider.")
    parser.add_argument("action", choices=("status", "settle"))
    parser.add_argument("--ledger", required=True, help="Path relative to artifacts/")
    parser.add_argument("--episode", required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args(argv)
    if not args.episode or Path(args.episode).name != args.episode or args.episode in (".", ".."):
        parser.error("episode must be one path component")
    path = owned(args.ledger)
    if not path.is_file():
        parser.error("an existing ledger is required")
    store = ResponseStore(Ledger.for_run(path), path.parent / "response-receipts" / args.episode)
    if store.context(args.request_id)["episode"] != args.episode:
        raise BudgetDenied("Receipt belongs to another episode")
    report = store.state(args.request_id)
    if args.action == "settle":
        report["settlement"] = store.settle(args.request_id)
    report.update(new_upstream_requests=0, new_reservations=0)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
