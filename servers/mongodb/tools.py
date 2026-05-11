"""
servers/mongodb/tools.py — pure tool functions for the mongodb MCP server
(lookup_customer, create_customer, update_job_status,
append_conversation_turn, persist_job, flag_for_human). No FastMCP
boilerplate; the MongoClient is initialized lazily via lru_cache so
importing this module does not open a connection.

All six tools share a process-wide MongoClient via _get_client(), which
mirrors the original module's retry/ping pattern from
prototype-v0/run_agent.py's get_collection() helper
(serverSelectionTimeoutMS=5000, ping check, 3 retries with 2 s backoff).
The ping forces a real round-trip so a misconfigured URI fails fast on
first use rather than silently caching a broken client.

If MONGODB_URI is unset or the cluster is unreachable, _get_client()
returns None and tools return {ok: false, error: "mongodb_unavailable"}
until connectivity is restored.

Required env vars:
  MONGODB_URI   Atlas connection string. No default — tools no-op without it.
  VOOMIE_DB     Database name. Default: "voomie".
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from functools import lru_cache
from typing import Any

import pymongo
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError


MONGODB_URI = os.environ.get("MONGODB_URI", "")
DB_NAME = os.environ.get("VOOMIE_DB", "voomie")

# Connection resilience knobs — mirror prototype-v0/run_agent.py defaults.
CONNECT_RETRIES = 3
CONNECT_DELAY_SECONDS = 2.0
SERVER_SELECTION_TIMEOUT_MS = 5000

# Authoritative phase vocabulary (SPEC.md §Interaction model + §state machine).
# Tools reject anything outside this set so the dashboard never has to render
# a phase string Voomie just made up.
VALID_PHASES = {
    "reading_message",
    "checking_attachments",
    "looking_up_customer",
    "resolving_stocks",
    "resolving_presses",
    "checking_coatings",
    "validating_dates",
    "drafting_reply",
    "validating_spec",
    "ready_for_review",
    "clarification_needed",
    "human_review",
    "done",
    "escalated",
}

VALID_TURN_ROLES = {"user", "agent", "agent_to_customer"}
VALID_TURN_STATUSES = {"sent", "draft", "pending_review"}

# SPEC.md §MongoDB schema: jobs._id is a J-number — "J" + 6 digits + "-" + 2 digits.
J_NUMBER_PATTERN = re.compile(r"^J\d{6}-\d{2}$")


def _connect() -> MongoClient | None:
    """Open the shared Atlas client, retrying transient failures.

    Mirrors prototype-v0/run_agent.py's get_collection() resilience pattern:
    serverSelectionTimeoutMS=5000, ping check on each attempt to fail fast on
    a misconfigured URI, retry with a 2 s backoff. Returns None after
    exhausting retries so the server can still start and surface a clean
    per-call error rather than dying at import time.
    """
    if not MONGODB_URI:
        print(
            "[mongodb_server] MONGODB_URI not set; tools will no-op until it is",
            file=sys.stderr,
            flush=True,
        )
        return None
    last_err: Exception | None = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            client = MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            )
            client.admin.command("ping")
            return client
        except PyMongoError as e:
            last_err = e
            print(
                f"[mongodb_server] connect attempt {attempt}/{CONNECT_RETRIES} "
                f"failed: {e}",
                file=sys.stderr,
                flush=True,
            )
            if attempt < CONNECT_RETRIES:
                time.sleep(CONNECT_DELAY_SECONDS)
        except Exception as e:  # noqa: BLE001 — match prototype's belt-and-suspenders catch
            last_err = e
            print(
                f"[mongodb_server] unexpected connect error attempt "
                f"{attempt}/{CONNECT_RETRIES}: {e}",
                file=sys.stderr,
                flush=True,
            )
            if attempt < CONNECT_RETRIES:
                time.sleep(CONNECT_DELAY_SECONDS)
    print(
        f"[mongodb_server] MongoDB unreachable after {CONNECT_RETRIES} "
        f"attempts: {last_err}",
        file=sys.stderr,
        flush=True,
    )
    return None


@lru_cache(maxsize=1)
def _get_client() -> MongoClient | None:
    """Lazy, process-wide MongoClient. Connects on first call, caches result."""
    return _connect()


@lru_cache(maxsize=1)
def _get_db():
    """Lazy database handle. None if the client is unavailable."""
    client = _get_client()
    return client[DB_NAME] if client is not None else None


def _customers():
    db = _get_db()
    return db["customers"] if db is not None else None


def _jobs():
    db = _get_db()
    return db["jobs"] if db is not None else None


def _conversations():
    db = _get_db()
    return db["conversations"] if db is not None else None


def _flags():
    db = _get_db()
    return db["flags"] if db is not None else None


def _connection_check() -> dict[str, Any] | None:
    """Return a connection-error envelope if the shared client is down; else None."""
    if _get_db() is None:
        return {"ok": False, "error": "mongodb_unavailable"}
    return None


def _normalize(value: Any) -> Any:
    """Recursively coerce ObjectId and datetime to JSON-friendly forms.

    MCP serializes structured tool responses as JSON; raw bson types would
    fail to encode. Stringify ObjectIds, ISO-format datetimes, walk nested
    dicts and lists.
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _resolve_customer_id(customer_id: Any) -> Any:
    """Best-effort coerce a stringified ObjectId back to ObjectId for lookup.

    create_customer returns customer_id as a stringified ObjectId. persist_job
    accepts whatever the agent supplies — usually that string. Try the
    ObjectId conversion; fall back to the raw value (covers the case of a
    schema variant that uses string _ids).
    """
    if isinstance(customer_id, ObjectId):
        return customer_id
    if isinstance(customer_id, str):
        try:
            return ObjectId(customer_id)
        except (InvalidId, TypeError):
            return customer_id
    return customer_id


def _set_job_phase(job_id: str, phase: str) -> int:
    """Internal helper: stamp phase + updated_at on the job. Returns matched_count.

    Used by both update_job_status (validated, public) and flag_for_human
    (which always sets phase=human_review as a side effect of the flag
    insert). No validation here — callers validate their own inputs.
    """
    if _get_db() is None:
        return 0
    result = _jobs().update_one(
        {"_id": job_id},
        {"$set": {"phase": phase, "updated_at": datetime.utcnow()}},
    )
    return result.matched_count


def lookup_customer(query: str) -> dict[str, Any]:
    """Look up a customer by email or name. Returns customer record and recent
    job history if found. Call this at the start of every conversation to
    check if the customer is known.

    Lookup order: email exact (case-insensitive) → name substring
    (case-insensitive). The first match wins. On hit, also returns the
    customer's 5 most recent jobs (created_at descending) as recent_jobs.

    Returns on miss: {ok: true, found: false}
    Returns on hit: {ok: true, found: true, customer: {...}, recent_jobs: [...]}
    Returns on error: {ok: false, error: <str>}
    """
    err = _connection_check()
    if err is not None:
        return err
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "query_required"}
    q = query.strip()
    try:
        customers = _customers()
        # Email exact, case-insensitive — anchored regex with escaped query.
        match = customers.find_one(
            {"email": {"$regex": f"^{re.escape(q)}$", "$options": "i"}}
        )
        if match is None:
            # Fallback: name substring, case-insensitive.
            match = customers.find_one(
                {"name": {"$regex": re.escape(q), "$options": "i"}}
            )
        if match is None:
            return {"ok": True, "found": False}

        customer_id = match["_id"]
        recent = list(
            _jobs()
            .find({"customer_id": str(customer_id)})
            .sort("created_at", pymongo.DESCENDING)
            .limit(5)
        )
        # Some persisted jobs may have customer_id stored as ObjectId rather
        # than its string form (depending on caller). Union the two queries
        # so lookup is robust to either.
        if not recent and isinstance(customer_id, ObjectId):
            recent = list(
                _jobs()
                .find({"customer_id": customer_id})
                .sort("created_at", pymongo.DESCENDING)
                .limit(5)
            )

        return {
            "ok": True,
            "found": True,
            "customer": _normalize(match),
            "recent_jobs": [_normalize(j) for j in recent],
        }
    except PyMongoError as e:
        return {"ok": False, "error": str(e)}


def create_customer(
    name: str,
    email: str,
    phone: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Create a new customer record. Call only after lookup_customer confirms
    the customer doesn't exist.

    Inserts {name, email, phone, shop_relationship_notes, first_seen, last_seen}.
    Both timestamps are UTC now. Rejects duplicate emails (case-insensitive).

    Returns on success: {ok: true, customer_id: <str>}
    Returns on duplicate: {ok: false, error: "duplicate_email"}
    Returns on error: {ok: false, error: <str>}
    """
    err = _connection_check()
    if err is not None:
        return err
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": "name_required"}
    if not isinstance(email, str):
        return {"ok": False, "error": "email_must_be_string"}

    customers = _customers()
    email_clean = email.strip()
    # Skip duplicate check for empty email (walk-in / anonymous customers).
    if email_clean:
        existing = customers.find_one(
            {"email": {"$regex": f"^{re.escape(email_clean)}$", "$options": "i"}}
        )
        if existing is not None:
            return {"ok": False, "error": "duplicate_email"}

    now = datetime.utcnow()
    doc = {
        "name": name,
        "email": email_clean,
        "phone": phone,
        "shop_relationship_notes": notes,
        "first_seen": now,
        "last_seen": now,
    }
    try:
        result = customers.insert_one(doc)
        return {"ok": True, "customer_id": str(result.inserted_id)}
    except PyMongoError as e:
        return {"ok": False, "error": str(e)}


def update_job_status(job_id: str, phase: str) -> dict[str, Any]:
    """Update the current processing phase of a job. Call this at each step
    so the CSR dashboard shows real-time progress. Use human-readable phase
    names from the allowed list.

    Allowed phases: reading_message, checking_attachments, looking_up_customer,
    resolving_stocks, resolving_presses, checking_coatings, validating_dates,
    drafting_reply, validating_spec, ready_for_review, clarification_needed,
    human_review, done, escalated.

    Returns on success: {ok: true, job_id, phase}
    Returns on invalid phase: {ok: false, error: "invalid_phase", phase, valid_phases}
    Returns on missing job: {ok: false, error: "job_not_found", job_id}
    """
    err = _connection_check()
    if err is not None:
        return err
    if not isinstance(job_id, str) or not job_id:
        return {"ok": False, "error": "job_id_required"}
    if phase not in VALID_PHASES:
        return {
            "ok": False,
            "error": "invalid_phase",
            "phase": phase,
            "valid_phases": sorted(VALID_PHASES),
        }
    try:
        matched = _set_job_phase(job_id, phase)
        if matched == 0:
            return {"ok": False, "error": "job_not_found", "job_id": job_id}
        return {"ok": True, "job_id": job_id, "phase": phase}
    except PyMongoError as e:
        return {"ok": False, "error": str(e)}


def append_conversation_turn(
    job_id: str,
    role: str,
    content: str,
    status: str = "sent",
) -> dict[str, Any]:
    """Append a turn to the conversation log. Use role='agent' for internal
    reasoning, role='agent_to_customer' with status='draft' for messages
    the CSR should review before sending to the customer.

    Upserts the conversation document on job_id; pushes the turn onto
    messages[] with a UTC timestamp. agent_to_customer turns with
    status="draft" are the CSR review queue — the dashboard surfaces them.

    Allowed roles: user, agent, agent_to_customer.
    Allowed statuses: sent, draft, pending_review.

    Returns on success: {ok: true, job_id, turn_count}
    Returns on invalid input: {ok: false, error: "invalid_role" | "invalid_status"}
    """
    err = _connection_check()
    if err is not None:
        return err
    if not isinstance(job_id, str) or not job_id:
        return {"ok": False, "error": "job_id_required"}
    if role not in VALID_TURN_ROLES:
        return {
            "ok": False,
            "error": "invalid_role",
            "role": role,
            "valid_roles": sorted(VALID_TURN_ROLES),
        }
    if status not in VALID_TURN_STATUSES:
        return {
            "ok": False,
            "error": "invalid_status",
            "status": status,
            "valid_statuses": sorted(VALID_TURN_STATUSES),
        }
    turn = {
        "role": role,
        "content": content,
        "status": status,
        "timestamp": datetime.utcnow(),
    }
    try:
        doc = _conversations().find_one_and_update(
            {"job_id": job_id},
            {
                "$push": {"messages": turn},
                "$setOnInsert": {"job_id": job_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        turn_count = len(doc.get("messages", [])) if doc else 0
        return {"ok": True, "job_id": job_id, "turn_count": turn_count}
    except PyMongoError as e:
        return {"ok": False, "error": str(e)}


def persist_job(job_record: dict) -> dict[str, Any]:
    """Persist the final job record to MongoDB. Call this after the shoptalk
    declaration has been validated. Side effect: updates customer.last_seen
    on the customer document referenced by job_record.customer_id.

    Required fields per SPEC.md §MongoDB schema:
      _id (J-number, e.g. "J123456-01"), parent_id, customer_id, status,
      phase, declaration_source, action_plan, attachments_metadata,
      out_of_scope_notes, due_date, rush, created_at, updated_at.

    The _id MUST match J-number pattern: "J" + 6 digits + "-" + 2 digits.
    Anything else is rejected before any write happens.

    Returns on success: {ok: true, job_id}
    Returns on bad _id: {ok: false, error: "invalid_job_id", job_id, expected_pattern}
    """
    err = _connection_check()
    if err is not None:
        return err
    if not isinstance(job_record, dict):
        return {"ok": False, "error": "job_record_must_be_dict"}

    job_id = job_record.get("_id")
    if not isinstance(job_id, str) or not J_NUMBER_PATTERN.match(job_id):
        return {
            "ok": False,
            "error": "invalid_job_id",
            "job_id": job_id,
            "expected_pattern": r"^J\d{6}-\d{2}$",
        }

    now = datetime.utcnow()
    doc = dict(job_record)
    doc.setdefault("created_at", now)
    doc["updated_at"] = now

    try:
        _jobs().replace_one({"_id": job_id}, doc, upsert=True)
        # Side effect — documented in the docstring above. Use the resolver
        # so a stringified ObjectId from create_customer round-trips correctly.
        customer_id = doc.get("customer_id")
        if customer_id:
            _customers().update_one(
                {"_id": _resolve_customer_id(customer_id)},
                {"$set": {"last_seen": now}},
            )
        return {"ok": True, "job_id": job_id}
    except PyMongoError as e:
        return {"ok": False, "error": str(e)}


def flag_for_human(
    job_id: str,
    reason: str,
    context: str,
) -> dict[str, Any]:
    """Flag a job for human review. Call when the agent cannot resolve an
    ambiguity after one clarifying question, or when a required field
    cannot be populated. Captures full context so the CSR can pick up
    without losing information.

    Inserts {job_id, type: "human_review", reason, context, resolved: false,
    created_at} into the flags collection AND sets the job's phase to
    "human_review" (best-effort — if the job document doesn't exist yet,
    the flag is still recorded).

    Returns on success: {ok: true, flag_id, job_id}
    """
    err = _connection_check()
    if err is not None:
        return err
    if not isinstance(job_id, str) or not job_id:
        return {"ok": False, "error": "job_id_required"}

    flag_doc = {
        "job_id": job_id,
        "type": "human_review",
        "reason": reason,
        "context": context,
        "resolved": False,
        "created_at": datetime.utcnow(),
    }
    try:
        result = _flags().insert_one(flag_doc)
        flag_id = str(result.inserted_id)
        # Best-effort phase update; missing job is not an error here — the
        # flag itself is the primary record.
        _set_job_phase(job_id, "human_review")
        return {"ok": True, "flag_id": flag_id, "job_id": job_id}
    except PyMongoError as e:
        return {"ok": False, "error": str(e)}
