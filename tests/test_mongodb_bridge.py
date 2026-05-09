"""
Test harness for the mongodb MCP bridge (lookup_customer, create_customer,
update_job_status, append_conversation_turn, persist_job, flag_for_human).

Each test spawns its own MCP server subprocess via lib.mcp_bridge.MCPBridge
and makes real MongoDB writes against the Atlas cluster behind MONGODB_URI.
No mocks. Tests use uuid4()-derived J-numbers and emails so they don't
collide with each other (or with seed data) and can run in any order.

Cleanup runs unconditionally in finally blocks: every test deletes every
document it created, regardless of assertion outcome. Re-running the suite
should leave the database in the same state it found it.

Mirrors the structure of test_parse_shoptalk_bridge.py /
test_registry_bridge.py: same fixture pattern, same assertion style.

Run:  pytest tests/test_mongodb_bridge.py -v
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pymongo
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

SERVER_PATH = REPO_ROOT / "tools" / "mongodb_server.py"
DB_NAME = os.environ.get("VOOMIE_DB", "voomie")

# Skip the whole module cleanly when the URI is missing — without this,
# import-time connection errors would derail collection.
pytestmark = pytest.mark.skipif(
    not os.environ.get("MONGODB_URI"),
    reason="MONGODB_URI not set; mongodb_server tests need a real Atlas cluster",
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_db():
    """Direct pymongo handle for verification + cleanup. Separate connection
    from the bridge subprocess — keeps the test's view of the DB independent
    from the path under test."""
    client = pymongo.MongoClient(
        os.environ["MONGODB_URI"], serverSelectionTimeoutMS=5000
    )
    client.admin.command("ping")
    yield client[DB_NAME]
    client.close()


@pytest.fixture
def bridge():
    """Per-test bridge: spawn the MCP server, yield, tear it down.

    MCP stdio_client doesn't inherit the parent's full environment — it only
    forwards a small whitelist (PATH, HOME, etc). We have to pass MONGODB_URI
    (and VOOMIE_DB if set) explicitly so the spawned server can connect.
    """
    server_env = {"MONGODB_URI": os.environ["MONGODB_URI"]}
    if "VOOMIE_DB" in os.environ:
        server_env["VOOMIE_DB"] = os.environ["VOOMIE_DB"]
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
        env=server_env,
    ) as b:
        yield b


def _structured(result: dict) -> dict:
    """Helper: extract the structured response from a bridge call_tool result."""
    structured = result["structured"]
    assert structured is not None, (
        f"No structured response from MCP server. Raw text was: {result['text']!r}"
    )
    assert isinstance(structured, dict), (
        f"Expected dict from MCP, got {type(structured).__name__}: {structured!r}"
    )
    return structured


def _short() -> str:
    """Short uuid fragment for unique-but-readable test identifiers."""
    return uuid.uuid4().hex[:10]


def _new_email(tag: str = "test") -> str:
    return f"voomie-{tag}-{_short()}@example.test"


def _new_jnum() -> str:
    """Generate a fresh J-number that satisfies J\\d{6}-\\d{2}.

    uuid4().int gives a fully random integer; pulling 6 digits from it and
    a 2-digit sequence is plenty of entropy for the test scope.
    """
    n = uuid.uuid4().int
    return f"J{n % 1_000_000:06d}-{(n // 1_000_000) % 100:02d}"


# ---------------------------------------------------------------------------
# Sanity: server advertises all six tools.
# ---------------------------------------------------------------------------


def test_server_advertises_all_six_tools(bridge):
    tools = set(bridge.list_tools())
    expected = {
        "lookup_customer",
        "create_customer",
        "update_job_status",
        "append_conversation_turn",
        "persist_job",
        "flag_for_human",
    }
    missing = expected - tools
    assert not missing, f"Server is missing tools: {missing}; advertised: {tools}"


# ---------------------------------------------------------------------------
# Customer lifecycle
# ---------------------------------------------------------------------------


def test_create_customer_then_lookup_by_email(bridge, raw_db):
    email = _new_email("email-lookup")
    customer_id = None
    try:
        created = _structured(bridge.call_tool("create_customer", {
            "name": "Email Lookup Tester",
            "email": email,
            "phone": "555-0100",
            "notes": "created by test_create_customer_then_lookup_by_email",
        }))
        assert created["ok"] is True, created
        customer_id = created["customer_id"]
        assert customer_id

        looked_up = _structured(bridge.call_tool("lookup_customer", {"query": email}))
        assert looked_up["ok"] is True
        assert looked_up["found"] is True
        assert looked_up["customer"]["email"] == email
        assert looked_up["customer"]["name"] == "Email Lookup Tester"
        assert looked_up["recent_jobs"] == []
    finally:
        if customer_id:
            from bson import ObjectId
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})


def test_lookup_customer_by_name_substring_case_insensitive(bridge, raw_db):
    """Name search is a case-insensitive substring match — 'rEyN' should
    find 'Sandra Reynolds'."""
    email = _new_email("name-lookup")
    customer_id = None
    try:
        created = _structured(bridge.call_tool("create_customer", {
            "name": f"Sandra Reynolds {_short()}",
            "email": email,
            "phone": "",
            "notes": "",
        }))
        assert created["ok"] is True, created
        customer_id = created["customer_id"]

        looked_up = _structured(bridge.call_tool("lookup_customer", {"query": "rEyN"}))
        # Substring match — may hit any customer whose name contains 'reyn',
        # but ours is freshly inserted so it's the most likely first hit.
        # We assert structurally: a hit was found and includes a customer dict.
        assert looked_up["ok"] is True
        assert looked_up["found"] is True
        # Defensive: verify our document is at least findable by the more
        # specific full-name substring.
        precise = _structured(bridge.call_tool(
            "lookup_customer", {"query": "Sandra Reynolds"}
        ))
        assert precise["found"] is True
        assert "Reynolds" in precise["customer"]["name"]
    finally:
        if customer_id:
            from bson import ObjectId
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})


def test_lookup_customer_unknown_email_returns_not_found(bridge):
    """Missing customer should return ok=true, found=false — not an error."""
    fake_email = _new_email("nonexistent")
    looked_up = _structured(bridge.call_tool("lookup_customer", {"query": fake_email}))
    assert looked_up["ok"] is True
    assert looked_up["found"] is False
    assert "error" not in looked_up


def test_create_customer_duplicate_email_rejected(bridge, raw_db):
    email = _new_email("dup")
    customer_id = None
    try:
        first = _structured(bridge.call_tool("create_customer", {
            "name": "First Insert",
            "email": email,
            "phone": "",
            "notes": "",
        }))
        assert first["ok"] is True
        customer_id = first["customer_id"]

        second = _structured(bridge.call_tool("create_customer", {
            "name": "Duplicate Attempt",
            "email": email,
            "phone": "",
            "notes": "",
        }))
        assert second["ok"] is False
        assert second["error"] == "duplicate_email"
    finally:
        if customer_id:
            from bson import ObjectId
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})


# ---------------------------------------------------------------------------
# Job phase updates
# ---------------------------------------------------------------------------


def test_update_job_status_with_valid_phase(bridge, raw_db):
    job_id = _new_jnum()
    customer_id = None
    try:
        # Need a job document for update_one to match. Create one via persist_job.
        cust = _structured(bridge.call_tool("create_customer", {
            "name": "Phase Tester", "email": _new_email("phase"),
            "phone": "", "notes": "",
        }))
        customer_id = cust["customer_id"]

        persisted = _structured(bridge.call_tool("persist_job", {"job_record": {
            "_id": job_id,
            "parent_id": None,
            "customer_id": customer_id,
            "status": "in_progress",
            "phase": "reading_message",
            "declaration_source": "#lang shoptalk\n# stub\n",
            "action_plan": "(stub)",
            "attachments_metadata": [],
            "out_of_scope_notes": [],
            "due_date": None,
            "rush": False,
        }}))
        assert persisted["ok"] is True

        updated = _structured(bridge.call_tool("update_job_status", {
            "job_id": job_id, "phase": "ready_for_review",
        }))
        assert updated["ok"] is True
        assert updated["job_id"] == job_id
        assert updated["phase"] == "ready_for_review"

        # Verify side effects in the raw collection.
        doc = raw_db["jobs"].find_one({"_id": job_id})
        assert doc is not None
        assert doc["phase"] == "ready_for_review"
        assert doc["updated_at"] is not None
    finally:
        raw_db["jobs"].delete_one({"_id": job_id})
        if customer_id:
            from bson import ObjectId
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})


def test_update_job_status_with_invalid_phase_returns_structured_error(bridge):
    """Unknown phase strings are rejected before any write happens."""
    result = _structured(bridge.call_tool("update_job_status", {
        "job_id": _new_jnum(), "phase": "doing_something_made_up",
    }))
    assert result["ok"] is False
    assert result["error"] == "invalid_phase"
    assert "valid_phases" in result
    assert "ready_for_review" in result["valid_phases"]


# ---------------------------------------------------------------------------
# Conversation log
# ---------------------------------------------------------------------------


def test_append_conversation_turn_creates_then_increments(bridge, raw_db):
    job_id = _new_jnum()
    try:
        first = _structured(bridge.call_tool("append_conversation_turn", {
            "job_id": job_id, "role": "user", "content": "Initial message",
            "status": "sent",
        }))
        assert first["ok"] is True
        assert first["job_id"] == job_id
        assert first["turn_count"] == 1

        second = _structured(bridge.call_tool("append_conversation_turn", {
            "job_id": job_id, "role": "agent", "content": "Reading message...",
            "status": "sent",
        }))
        assert second["ok"] is True
        assert second["turn_count"] == 2
    finally:
        raw_db["conversations"].delete_one({"job_id": job_id})


def test_append_agent_to_customer_draft_is_retrievable(bridge, raw_db):
    """agent_to_customer + status=draft is the CSR review queue. Verify the
    turn lands with the correct shape so the dashboard query can find it."""
    job_id = _new_jnum()
    try:
        result = _structured(bridge.call_tool("append_conversation_turn", {
            "job_id": job_id,
            "role": "agent_to_customer",
            "content": "Draft reply for CSR review",
            "status": "draft",
        }))
        assert result["ok"] is True

        doc = raw_db["conversations"].find_one({"job_id": job_id})
        assert doc is not None
        assert len(doc["messages"]) == 1
        turn = doc["messages"][0]
        assert turn["role"] == "agent_to_customer"
        assert turn["status"] == "draft"
        assert turn["content"] == "Draft reply for CSR review"
        assert turn["timestamp"] is not None
    finally:
        raw_db["conversations"].delete_one({"job_id": job_id})


# ---------------------------------------------------------------------------
# Job persistence
# ---------------------------------------------------------------------------


def test_persist_job_writes_record_and_updates_customer_last_seen(bridge, raw_db):
    """persist_job has a documented side effect: bump customer.last_seen.
    Verify both the write and the side effect."""
    from bson import ObjectId

    email = _new_email("persist")
    job_id = _new_jnum()
    customer_id = None
    try:
        cust = _structured(bridge.call_tool("create_customer", {
            "name": "Persist Tester", "email": email, "phone": "", "notes": "",
        }))
        customer_id = cust["customer_id"]

        # Capture the customer's last_seen pre-persist for comparison.
        before = raw_db["customers"].find_one({"_id": ObjectId(customer_id)})
        last_seen_before = before["last_seen"]

        persisted = _structured(bridge.call_tool("persist_job", {"job_record": {
            "_id": job_id,
            "parent_id": None,
            "customer_id": customer_id,
            "status": "ready_for_review",
            "phase": "ready_for_review",
            "declaration_source": "#lang shoptalk\n# stub\n",
            "action_plan": "(stub)",
            "attachments_metadata": [],
            "out_of_scope_notes": [],
            "due_date": None,
            "rush": False,
        }}))
        assert persisted["ok"] is True
        assert persisted["job_id"] == job_id

        job_doc = raw_db["jobs"].find_one({"_id": job_id})
        assert job_doc is not None
        assert job_doc["customer_id"] == customer_id
        assert job_doc["phase"] == "ready_for_review"

        # Side effect: last_seen bumped forward.
        after = raw_db["customers"].find_one({"_id": ObjectId(customer_id)})
        assert after["last_seen"] >= last_seen_before
    finally:
        raw_db["jobs"].delete_one({"_id": job_id})
        if customer_id:
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})


def test_persist_job_with_malformed_jnumber_rejected(bridge):
    """Anything not matching ^J\\d{6}-\\d{2}$ is rejected before any write."""
    bad_record = {
        "_id": "not-a-jnumber",
        "customer_id": "irrelevant",
        "status": "in_progress",
        "phase": "reading_message",
    }
    result = _structured(bridge.call_tool("persist_job", {"job_record": bad_record}))
    assert result["ok"] is False
    assert result["error"] == "invalid_job_id"
    assert "expected_pattern" in result


# ---------------------------------------------------------------------------
# Human review flag
# ---------------------------------------------------------------------------


def test_flag_for_human_creates_flag_and_sets_phase(bridge, raw_db):
    from bson import ObjectId

    job_id = _new_jnum()
    customer_id = None
    flag_id = None
    try:
        cust = _structured(bridge.call_tool("create_customer", {
            "name": "Flag Tester", "email": _new_email("flag"),
            "phone": "", "notes": "",
        }))
        customer_id = cust["customer_id"]

        persisted = _structured(bridge.call_tool("persist_job", {"job_record": {
            "_id": job_id, "parent_id": None, "customer_id": customer_id,
            "status": "in_progress", "phase": "drafting_reply",
            "declaration_source": "stub", "action_plan": "(stub)",
            "attachments_metadata": [], "out_of_scope_notes": [],
            "due_date": None, "rush": False,
        }}))
        assert persisted["ok"] is True

        flagged = _structured(bridge.call_tool("flag_for_human", {
            "job_id": job_id,
            "reason": "ambiguous_stock_after_clarification",
            "context": "Customer asked for 'heavy gloss' — registry returned 4 matches.",
        }))
        assert flagged["ok"] is True
        assert flagged["job_id"] == job_id
        flag_id = flagged["flag_id"]
        assert flag_id

        flag_doc = raw_db["flags"].find_one({"_id": ObjectId(flag_id)})
        assert flag_doc is not None
        assert flag_doc["job_id"] == job_id
        assert flag_doc["type"] == "human_review"
        assert flag_doc["resolved"] is False

        # Side effect: job phase is now human_review.
        job_doc = raw_db["jobs"].find_one({"_id": job_id})
        assert job_doc["phase"] == "human_review"
    finally:
        if flag_id:
            raw_db["flags"].delete_one({"_id": ObjectId(flag_id)})
        raw_db["jobs"].delete_one({"_id": job_id})
        if customer_id:
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})


# ---------------------------------------------------------------------------
# End-to-end consistency
# ---------------------------------------------------------------------------


def test_full_flow_customer_job_conversation_flag_consistent(bridge, raw_db):
    """Stitches create_customer → persist_job → 3× append_conversation_turn →
    flag_for_human and verifies all four collections agree on the IDs.

    The CSR dashboard reads from MongoDB; if any cross-collection link is
    wrong here, the dashboard renders an inconsistent job."""
    from bson import ObjectId

    email = _new_email("e2e")
    job_id = _new_jnum()
    customer_id = None
    flag_id = None
    try:
        cust = _structured(bridge.call_tool("create_customer", {
            "name": "End-to-End Customer", "email": email,
            "phone": "555-0199", "notes": "Used by full-flow test.",
        }))
        assert cust["ok"] is True
        customer_id = cust["customer_id"]

        persisted = _structured(bridge.call_tool("persist_job", {"job_record": {
            "_id": job_id,
            "parent_id": None,
            "customer_id": customer_id,
            "status": "in_progress",
            "phase": "reading_message",
            "declaration_source": "#lang shoptalk\n# e2e stub\n",
            "action_plan": "(stub)",
            "attachments_metadata": [{"filename": "art.pdf", "pages": 2}],
            "out_of_scope_notes": [],
            "due_date": None,
            "rush": False,
        }}))
        assert persisted["ok"] is True

        for i, (role, content, status) in enumerate([
            ("user", "Need 1000 4×6 postcards.", "sent"),
            ("agent", "Looked up customer and resolved stock.", "sent"),
            ("agent_to_customer", "Confirming 100# Gloss Cover — OK?", "draft"),
        ], start=1):
            turn = _structured(bridge.call_tool("append_conversation_turn", {
                "job_id": job_id, "role": role, "content": content, "status": status,
            }))
            assert turn["ok"] is True
            assert turn["turn_count"] == i

        flagged = _structured(bridge.call_tool("flag_for_human", {
            "job_id": job_id,
            "reason": "needs_csr_signoff_on_draft",
            "context": "Draft reply ready; CSR should review and send.",
        }))
        assert flagged["ok"] is True
        flag_id = flagged["flag_id"]

        # Cross-collection consistency: same job_id ties customer, job,
        # conversation, and flag together.
        customer_doc = raw_db["customers"].find_one({"_id": ObjectId(customer_id)})
        job_doc = raw_db["jobs"].find_one({"_id": job_id})
        conv_doc = raw_db["conversations"].find_one({"job_id": job_id})
        flag_doc = raw_db["flags"].find_one({"_id": ObjectId(flag_id)})

        assert customer_doc is not None
        assert job_doc is not None and job_doc["customer_id"] == customer_id
        assert conv_doc is not None and len(conv_doc["messages"]) == 3
        assert flag_doc is not None and flag_doc["job_id"] == job_id

        # The flag side-effect should have driven the job to human_review.
        job_doc_after = raw_db["jobs"].find_one({"_id": job_id})
        assert job_doc_after["phase"] == "human_review"
    finally:
        if flag_id:
            from bson import ObjectId
            raw_db["flags"].delete_one({"_id": ObjectId(flag_id)})
        raw_db["jobs"].delete_one({"_id": job_id})
        raw_db["conversations"].delete_one({"job_id": job_id})
        if customer_id:
            from bson import ObjectId
            raw_db["customers"].delete_one({"_id": ObjectId(customer_id)})
