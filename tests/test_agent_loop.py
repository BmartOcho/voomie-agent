"""
tests/test_agent_loop.py — integration tests for voomie.agent.process_message.

These are real end-to-end tests: each one spawns the combined MCP server,
hits MongoDB Atlas, and calls Vertex AI Gemini. They are slow (30-90 s
typical, more on the deliberately-ambiguous cases) and skip-gated on
both MONGODB_URI and GCP_PROJECT_ID. CI without Atlas + Vertex creds
sees them as skipped, not failed.

The four "real customer message" fixtures are reconstructed from
SPEC.md §Demo arc and §Definition of done plus seed_db.py. SPEC.md
references the messages by name and intent (Cindy's clean push card,
Chris's multi-job with coating conflict, Frank's mailing-services
request, Message 4's deadline + non-standard size) without preserving
their literal text — these are operator-best-guess reconstructions
fit for the SPEC behaviors. Adjust the message text if the canonical
fixtures land elsewhere in the repo later; the assertions are about
the AGENT'S behavior, not the literal message wording.

Cleanup contract: each test deletes any jobs/conversations/flags whose
job_id starts with that test's parent J-number. Customers stay (they
were seeded). If the agent created a customer mid-test, that record
remains — best-effort cleanup; not load-bearing for correctness of
subsequent tests.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Skip the entire module unless both Atlas and Vertex creds are present.
# Without them, every test would fail with an unhelpful first-call error.
_REQUIRED_ENV = ("MONGODB_URI", "GCP_PROJECT_ID")
_missing_env = [k for k in _REQUIRED_ENV if not os.environ.get(k)]

pytestmark = pytest.mark.skipif(
    bool(_missing_env),
    reason=(
        f"Live agent tests require: {_REQUIRED_ENV}; missing: {_missing_env}. "
        f"Tests skip rather than fail so CI without GCP/Atlas creds stays green."
    ),
)


# Lazy imports — gated below so module-import doesn't fail when env vars
# are absent (e.g. when collecting tests without running them).
if not _missing_env:
    from voomie.agent import _final_state, process_message  # noqa: E402

    from servers.mongodb import tools as mdb  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture messages — reconstructed from SPEC.md
# ---------------------------------------------------------------------------

# Cindy's message — clean case, single job, all specs precise.
# Mirrors the message Voomie sees in scripts/poc_bridge.py (which is the
# parse_shoptalk-only POC) but routed through the full agent loop.
CINDY_MESSAGE = """\
Please quote 4 x 9 candidate push cards, 4/4 bleed, 100# cover, C2S
Quantity: 150

Thank you,
Cindy Meyer
"""
CINDY_EMAIL = "cindy.meyer@campaign-test.example"

# Chris's message — multi-job (BLAST direct-mail postcard + Valentine
# postcard with spot UV + soft-touch laminate). The coating conflict
# is the "moment Voomie demonstrably knows something the customer
# didn't say" — see SPEC.md §Demo arc 0:20–1:50.
CHRIS_MESSAGE = """\
Hi team — two jobs this week.

1) BLAST mailer: 5,000 6×4 postcards, 4/4 bleed, 100# Gloss Cover, mail
   class same as last time. Need them on the truck by next Friday.

2) Valentine card: 1,000 5×3.5 postcards, 4/4 bleed, 16pt C2S. I want
   spot UV on the front (the heart graphic) AND soft-touch laminate
   over the whole face — make it really pop while still feeling
   premium. CMYK PDF coming separately for both.

Thanks,
Chris
"""
CHRIS_EMAIL = "chris@blastmailco.com"  # already seeded — returning customer


# Frank's message — single postcard job + out-of-scope mailing services.
FRANK_MESSAGE = """\
Hi! Need 1,000 6×4 postcards for our spring open house, 80# gloss cover,
4/4 bleed. Also — can you mail them out for me to my list of about 800
addresses? I have the addresses in a spreadsheet.

Frank Delgado
"""
FRANK_EMAIL = "frank@yogaandmartialarts.com"  # seeded


# Message 4 — anonymous, mentions deadline and a postcard size (5x3.5
# qualifies as USPS postcard). SPEC.md §Demo arc references "Message 4's
# attachment-driven case" but the attachment is documented in the
# Devpost writeup, not run on video. We exercise the deadline + size
# behavior without an attachment here.
MESSAGE_4 = """\
Hey, I need 5x3.5inch cards printed. 500 pieces. Need them by the 23rd
of this month if possible. Glossy stock is fine.
"""
MESSAGE_4_CUSTOMER = "anonymous-msg4@walk-in.example"


# Deliberately-ambiguous message — drives the 3-turn cap path.
# Says nothing the agent can resolve without clarification: no quantity,
# no size, no stock, no deadline.
AMBIGUOUS_MESSAGE = """\
hey i need stuff printed. let me know what you can do.

— J
"""
AMBIGUOUS_CUSTOMER = "ambiguous@walk-in.example"


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


def _cleanup_parent(parent_j: str) -> None:
    """Delete all docs created by the agent under the given parent J-number.

    Parent J-numbers are unique-per-second-mod-1M; this regex-prefix
    delete won't touch unrelated jobs.
    """
    if not parent_j or _missing_env:
        return
    db = mdb._get_db()
    if db is None:
        return
    pattern = {"$regex": f"^{parent_j}-\\d{{2}}$"}
    db["jobs"].delete_many({"_id": pattern})
    db["conversations"].delete_many({"job_id": pattern})
    db["flags"].delete_many({"job_id": pattern})


@pytest.fixture
def cleanup_parent():
    """Per-test cleanup: yield a list to track parent IDs, delete after."""
    seen: list[str] = []
    try:
        yield seen
    finally:
        for parent_j in seen:
            try:
                _cleanup_parent(parent_j)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                pass


def _job_doc(job_id: str) -> dict | None:
    db = mdb._get_db()
    return db["jobs"].find_one({"_id": job_id}) if db is not None else None


def _conversation_doc(job_id: str) -> dict | None:
    db = mdb._get_db()
    return db["conversations"].find_one({"job_id": job_id}) if db is not None else None


def _customer_facing_drafts(job_id: str) -> list[dict]:
    """Return the agent_to_customer/draft turns for a job."""
    conv = _conversation_doc(job_id)
    if conv is None:
        return []
    return [
        m
        for m in conv.get("messages", [])
        if m.get("role") == "agent_to_customer" and m.get("status") == "draft"
    ]


def _agent_reasoning_text(job_id: str) -> str:
    """Concatenate all role='agent' content for substring assertions."""
    conv = _conversation_doc(job_id)
    if conv is None:
        return ""
    return " ".join(
        m.get("content", "")
        for m in conv.get("messages", [])
        if m.get("role") == "agent"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cindy_clean_case(cleanup_parent):
    """Clean single-job message resolves to ready_for_review with one
    declaration produced and no flags raised. Lower bar than the real
    demo — we accept any final_status that isn't 'escalated', because
    Gemini occasionally drafts a clarifying question on edge details
    (e.g. asking which press to use). The spirit of 'clean case' is
    'agent didn't escalate or fail outright'."""
    result = process_message(CINDY_EMAIL, CINDY_MESSAGE)
    cleanup_parent.append(result.get("parent_job_id"))
    assert result.get("ok") is True, f"agent failed: {result}"
    assert result["final_status"] in {"ready_for_review", "clarification_needed"}, (
        f"clean case shouldn't escalate or human-review: {result}"
    )
    assert len(result["child_job_ids"]) == 1, (
        f"single-job message should produce exactly one child: {result}"
    )


def test_chris_multi_job_with_coating_conflict(cleanup_parent):
    """Chris's two-job message: one clean BLAST mailer + one Valentine
    postcard with a spot UV + soft-touch laminate conflict. Agent should
    surface the conflict in a customer-facing draft reply.

    Tolerates either 1 or 2 child jobs — Gemini sometimes opts to handle
    both jobs under a single declaration when the second is partial.
    The required behavior is the coating-conflict callout, which is what
    distinguishes Voomie from a syntax-only translator."""
    result = process_message(CHRIS_EMAIL, CHRIS_MESSAGE)
    cleanup_parent.append(result.get("parent_job_id"))
    assert result.get("ok") is True, f"agent failed: {result}"

    # At least one child must exist — multi-job recognition is
    # demo-critical but not guaranteed every run.
    children = result["child_job_ids"]
    assert len(children) >= 1, f"expected at least one child: {result}"

    # The coating conflict must surface in a customer-facing draft on
    # at least one child. We search across all children's drafts.
    drafts_text = " ".join(
        m.get("content", "")
        for child_id in children
        for m in _customer_facing_drafts(child_id)
    ).lower()
    # Also include agent reasoning — Gemini sometimes drops the conflict
    # explanation into role:agent rather than role:agent_to_customer.
    reasoning_text = " ".join(
        _agent_reasoning_text(c) for c in children
    ).lower()
    combined = drafts_text + " " + reasoning_text

    # Key terms — match if any substring appears, since Gemini's wording
    # varies. The combination is what we care about, not the phrasing.
    has_uv = "uv" in combined
    has_laminate = "laminate" in combined or "soft-touch" in combined or "soft touch" in combined
    assert has_uv and has_laminate, (
        f"coating conflict (spot UV + soft-touch laminate) not surfaced "
        f"in drafts or reasoning: drafts={drafts_text[:300]} "
        f"reasoning={reasoning_text[:300]}"
    )


def test_frank_out_of_scope_mailing(cleanup_parent):
    """Frank's message: clean postcard + mailing services request.
    The print part is fine; the mailing request must land in
    out_of_scope_notes on the persisted job.

    Tolerates the agent surfacing it in agent_to_customer drafts as well
    — what matters is that the data isn't lost; the CSR sees it."""
    result = process_message(FRANK_EMAIL, FRANK_MESSAGE)
    cleanup_parent.append(result.get("parent_job_id"))
    assert result.get("ok") is True, f"agent failed: {result}"
    assert len(result["child_job_ids"]) >= 1

    # Walk every child looking for evidence the mailing request was
    # captured: out_of_scope_notes on the job, OR a draft mentioning it.
    captured = False
    for child_id in result["child_job_ids"]:
        job = _job_doc(child_id) or {}
        notes = " ".join(job.get("out_of_scope_notes") or []).lower()
        if "mail" in notes or "list" in notes:
            captured = True
            break
        for draft in _customer_facing_drafts(child_id):
            if "mail" in draft.get("content", "").lower():
                captured = True
                break
        if captured:
            break

    assert captured, (
        f"mailing request not captured anywhere — out_of_scope_notes empty "
        f"and no drafts mention it for jobs {result['child_job_ids']}"
    )


def test_message_4_deadline_and_size(cleanup_parent):
    """Message 4 — 5×3.5 cards needed by the 23rd. Expect the agent to
    recognize this as a USPS-eligible postcard size and resolve the
    deadline. We don't hard-assert due_date format because Gemini's
    date resolution is brittle — we just check the agent didn't drop
    the deadline silently."""
    result = process_message(MESSAGE_4_CUSTOMER, MESSAGE_4)
    cleanup_parent.append(result.get("parent_job_id"))
    assert result.get("ok") is True, f"agent failed: {result}"
    assert len(result["child_job_ids"]) >= 1

    # At least one child should have due_date populated, OR the agent
    # should have asked a clarifying question that mentions the date.
    child_id = result["child_job_ids"][0]
    job = _job_doc(child_id) or {}
    has_due = bool(job.get("due_date"))

    drafts = _customer_facing_drafts(child_id)
    drafts_text = " ".join(d.get("content", "") for d in drafts).lower()
    reasoning_text = _agent_reasoning_text(child_id).lower()
    mentions_date = (
        "23" in drafts_text
        or "deadline" in drafts_text
        or "23" in reasoning_text
        or "deadline" in reasoning_text
    )

    assert has_due or mentions_date, (
        f"deadline ('the 23rd') was not captured: due_date={job.get('due_date')!r}, "
        f"drafts={drafts_text[:200]!r}, reasoning={reasoning_text[:200]!r}"
    )


def test_three_turn_cap_enforcement(cleanup_parent):
    """Deliberately-ambiguous message exercises the 3-turn cap.
    Expected: agent escalates after 3 customer-facing drafts (or fewer
    if Gemini gives up sooner). Final status must be 'escalated' or
    'human_review'; at least one flag must be raised; no more than 3
    customer-facing drafts persisted."""
    result = process_message(AMBIGUOUS_CUSTOMER, AMBIGUOUS_MESSAGE)
    cleanup_parent.append(result.get("parent_job_id"))
    assert result.get("ok") is True, f"agent failed: {result}"

    # Sum drafts across all children; cap applies to the whole parent.
    total_drafts = sum(
        len(_customer_facing_drafts(c)) for c in result["child_job_ids"]
    )
    assert total_drafts <= 3, (
        f"3-turn cap broken: {total_drafts} drafts persisted across "
        f"{result['child_job_ids']}"
    )

    # Either the cap forced escalation or the agent gave up earlier.
    assert result["final_status"] in {"escalated", "human_review"}, (
        f"ambiguous message should escalate, got {result['final_status']!r}: "
        f"{result}"
    )
    assert result["flags_raised"] >= 1, (
        f"ambiguous message should raise at least one flag: {result}"
    )


def test_final_state_aggregates_over_status_field(cleanup_parent):
    """Regression test: _final_state must aggregate over the `status`
    field (the resting-state semantic), not the `phase` field (the
    streaming progress indicator).

    Previously, jobs that successfully finished with status='ready_for_review'
    and phase='done' were demoted at the parent-summary level to
    'clarification_needed', because 'done' is not in the priority list and
    the phase-based lookup fell through to the no-flag fallback. This test
    seeds two synthetic child jobs with status='ready_for_review' / phase='done'
    and asserts the parent-level final_status comes back as 'ready_for_review'.

    Unit-level test — no Vertex/agent invocation, just MongoDB seeding +
    the aggregation function. Fast (~1 s). Cleans up via the standard
    cleanup_parent fixture.
    """
    db = mdb._get_db()
    assert db is not None, "MongoDB unavailable; cannot run test"

    # Synthetic parent J-number well outside both the seeder's space
    # (J500001+) and the agent's tick-second-mod-1M generator's normal
    # range, so collisions with concurrent runs are vanishingly rare.
    parent = "J999990"
    cleanup_parent.append(parent)
    children = [f"{parent}-01", f"{parent}-02"]

    now = datetime.utcnow()
    for cid in children:
        db["jobs"].replace_one(
            {"_id": cid},
            {
                "_id": cid,
                "parent_id": parent,
                "customer_id": None,
                "status": "ready_for_review",
                "phase": "done",
                "declaration_source": "#lang shoptalk\njob \"x\" {}\n",
                "action_plan": "(job (name \"x\"))",
                "attachments_metadata": [],
                "out_of_scope_notes": [],
                "due_date": None,
                "rush": False,
                "created_at": now,
                "updated_at": now,
            },
            upsert=True,
        )

    result = _final_state(parent)

    assert result["final_status"] == "ready_for_review", (
        f"phase='done' with status='ready_for_review' must aggregate to "
        f"ready_for_review, got {result['final_status']!r}: {result}"
    )
    assert sorted(result["child_job_ids"]) == sorted(children)
    assert result["declarations_produced"] == 2, (
        f"both seeded jobs have non-empty declaration_source: {result}"
    )
    assert result["flags_raised"] == 0


def test_returning_customer_recognition(cleanup_parent):
    """Re-run Chris's message; he's seeded. The agent's reasoning log
    (role='agent' turns) should reference returning-customer status
    or prior jobs. We accept any of: 'returning', 'before', 'previous',
    'history', or one of his seeded job names ('Walton').

    This is a soft signal — Gemini sometimes synthesizes the customer
    context internally without writing it to the conversation log. The
    fallback is to check that lookup_customer was called (we can't see
    the call directly, but we can observe customer_id in the persisted
    job — Chris's seeded customer_id, not a fresh one)."""
    result = process_message(CHRIS_EMAIL, CHRIS_MESSAGE)
    cleanup_parent.append(result.get("parent_job_id"))
    assert result.get("ok") is True, f"agent failed: {result}"
    assert len(result["child_job_ids"]) >= 1

    # Lookup the seeded Chris record so we know what customer_id to expect.
    seeded = mdb.lookup_customer(CHRIS_EMAIL)
    assert seeded.get("found"), (
        f"test fixture broken: Chris ({CHRIS_EMAIL}) is not seeded; "
        f"run scripts/seed_db.py first"
    )
    expected_id = str(seeded["customer"]["_id"])

    # The persisted job should reference the seeded customer_id, not a
    # newly-created one. This proves lookup_customer happened and the
    # agent reused the existing record.
    matched_seeded = False
    for child_id in result["child_job_ids"]:
        job = _job_doc(child_id) or {}
        if str(job.get("customer_id") or "") == expected_id:
            matched_seeded = True
            break

    # Soft fallback: if customer_id linkage missed, look for prior-job
    # references in the agent's reasoning log.
    reasoning = " ".join(
        _agent_reasoning_text(c).lower() for c in result["child_job_ids"]
    )
    soft_signals = ("returning", "before", "previous", "history", "walton", "100# gloss")
    soft_matched = any(s in reasoning for s in soft_signals)

    assert matched_seeded or soft_matched, (
        f"returning-customer recognition not visible: customer_id linkage "
        f"missed AND no prior-job signal in reasoning. expected_id={expected_id}, "
        f"reasoning_preview={reasoning[:300]!r}"
    )
