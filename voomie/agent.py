"""
voomie/agent.py — the Voomie agent loop.

Wires Vertex AI Gemini to the combined MCP server (tools/voomie_server.py)
and runs the SPEC.md §Multi-step mission state machine end-to-end on a
single customer message. This is the largest piece of business logic in
the repo; everything else is plumbing for it.

The agent is synchronous at the function boundary. Internally,
`MCPBridge` runs the MCP stdio session on its own asyncio loop; we
dispatch tool calls through it without exposing any async surface.

Customer-facing turn cap: SPEC.md §Conversation flow as state machine
locks the customer-facing turn count at 3. We track it directly here
rather than relying on Gemini's prompt-following — the cap is a hard
correctness guarantee, not a heuristic. After 3 drafted
agent_to_customer turns, we force escalation regardless of what Gemini
asks to do next.

Pre-creation of the first child job stub: SPEC.md §Multi-step mission
step 1 says "Create job record(s) in MongoDB immediately." update_job_status
returns job_not_found until persist_job has been called for that _id, so
the bridge would surface a stream of misleading errors on every early
phase update without the stub. We pre-create J######-01 with a
placeholder customer_id; the agent's eventual persist_job is an upsert
(replace_one with upsert=True) that backfills the real fields.

Determinism caveat: Gemini Flash sometimes skips update_job_status calls
or doesn't append every reasoning turn it should. The system prompt is
the steering wheel — we don't try to enforce step-by-step compliance
through guard rails beyond the 3-turn cap.
"""

from __future__ import annotations

import difflib
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import vertexai  # noqa: E402
from vertexai.generative_models import (  # noqa: E402
    FunctionDeclaration,
    GenerativeModel,
    Part,
    Tool,
)

from lib.mcp_bridge import MCPBridge  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "pressflow-hackathon")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

# Default to Pro: Flash repeatedly emits parameter typos (e.g. joba_id) and
# regresses to Python source inside text parts (Vertex Finish reason 9) on
# tool-heavy turns. Pro is more reliable on agentic loops. Override with
# VOOMIE_MODEL or the --model CLI flag to flip back to Flash for cost runs.
DEFAULT_MODEL_NAME = os.environ.get("VOOMIE_MODEL", "gemini-2.5-pro")
MODEL_NAME = DEFAULT_MODEL_NAME  # back-compat alias for any external import

SERVER_PATH = REPO_ROOT / "tools" / "voomie_server.py"

# Backstop on the agent loop. Even with the 3-turn customer-facing cap,
# Gemini can churn on internal tool calls — this prevents a runaway loop.
MAX_AGENT_TURNS = 40

# 3-turn customer-facing cap from SPEC.md §Conversation flow.
CUSTOMER_FACING_TURN_CAP = 3

# Cap on consecutive Vertex Finish-reason-9 (malformed function call) turns
# before we give up and force-escalate. Pro should rarely emit even one;
# Flash can produce these in bursts. Three strikes keeps a recoverable hiccup
# from killing the run while still preventing an infinite retry loop.
MAX_CONSECUTIVE_MALFORMED_TURNS = 3

# difflib.SequenceMatcher ratio threshold for parameter-name typo correction.
# 0.8 catches single-character insertions/deletions/transpositions ("joba_id"
# vs "job_id" → ~0.86) without firing on genuinely different keys.
PARAM_TYPO_SIMILARITY_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
#
# {today_iso} is substituted at runtime so date-validation step 6 has an
# anchor — Gemini doesn't know what "today" is otherwise.

SYSTEM_PROMPT = """You are Voomie, an AI assistant for Voom Group, a commercial printing shop.

Today's date is {today_iso}. Use this when validating customer deadlines.

Your job: take a messy natural-language customer print-job inquiry and produce a validated shoptalk job declaration, ready for a CSR to quote and schedule. You do NOT quote prices, schedule production, or handle mailing services — that's the CSR's job. You produce the structured spec; they handle the rest.

You have access to 11 tools across 5 domains:
- parse_shoptalk: validates a shoptalk source string against the language grammar
- query_stock_registry, query_press_registry: look up real stocks and presses in Voom Group's actual inventory
- lookup_customer, create_customer: find or create customer records in MongoDB
- update_job_status: stream phase updates to the CSR dashboard so they see Voomie working in real time
- append_conversation_turn: persist conversation turns (user messages, your reasoning, draft replies)
- persist_job: write the final job record to MongoDB
- flag_for_human: escalate to CSR review when you can't resolve something
- inspect_pdf: read PDF metadata when a customer attaches a file
- render_preview: render a shoptalk action plan to a preview PDF (use only when explicitly asked)

Run this 10-step mission for every message:

1. Acknowledge and classify. Call update_job_status("reading_message"). If the message contains multiple distinct print jobs, process each separately under sibling J-numbers (e.g., J123456-01 and J123456-02 sharing parent J123456). If you're unsure whether it's one job or multiple, prefer one job — over-splitting is worse than under-splitting.

2. Identify customer. Call lookup_customer with the email if provided, otherwise the name. If found, note their job history. If not found, you'll create them in step 10.

3. Inspect attachments. If PDFs are attached, call inspect_pdf on each. Use trim size, color space, and bleed presence to inform the declaration. If non-PDF formats are attached (JPG, PNG, AI, INDD), recognize them and ask the customer for a PDF in your draft reply — do not attempt conversion.

4. Resolve specs. Use query_stock_registry and query_press_registry. For approximate language ("something like 80# cover"), pass it through text_search — the registry handles fuzzy matching. For precise specs ("16pt C2S"), also use text_search. Combine with structural filters (basis_weight_min, coating, etc.) only when the customer specifies multiple constraints.

5. Check coatings. shoptalk has no coating compatibility logic, so you own this. Known conflicts: spot UV under soft-touch laminate dulls the UV's tactile contrast (ask which they prioritize); foil over laminate doesn't adhere well; multiple coatings on coated stock often need test runs. If you see a conflict, flag it in your draft reply.

6. Validate dates. shoptalk doesn't validate due dates. If a deadline is mentioned, resolve to a real calendar date relative to today's date (today is {today_iso}). Sanity-check it isn't in the past or absurdly far out. Populate due: and rush: fields in the declaration.

7. Resolve or escalate. For each ambiguity, batch all clarifying questions into a single customer-facing message per turn. At most one such turn per ambiguity. Hard cap of 3 customer-facing turns total before mandatory escalation. If still stuck, produce a partial declaration with `// HUMAN_REVIEW_NEEDED:` comments and call flag_for_human.

8. Acknowledge out-of-scope. If the customer requested mailing services, list management, design work, or anything else outside shoptalk, capture those as out_of_scope_notes on the job record without trying to spec them.

9. Validate declaration. Call parse_shoptalk on your draft. If it parses cleanly, persist. If it errors, attempt one self-correction based on the structured error returned. If it errors again, flag for human.

10. Persist and notify. Call persist_job with the complete record. Call update_job_status to "ready_for_review" or "clarification_needed" or "human_review" as appropriate.

Throughout: call update_job_status whenever you transition to a new logical phase. Use append_conversation_turn for every meaningful step — your reasoning (role: "agent"), the customer's message (role: "user"), and any draft replies you want the CSR to send (role: "agent_to_customer", status: "draft"). The dashboard reads these in real time.

Coating compatibility rules to apply:
- Spot UV under soft-touch laminate: laminate dulls UV's tactile contrast — flag this and ask if customer wants UV pop or soft-touch feel
- Foil over laminate: poor adhesion, recommend foil before laminate
- Multiple coatings on offset: may require test run, flag for CSR scheduling
- Soft-touch laminate on uncoated stock: incompatible, recommend coated stock
- Spot UV on uncoated stock: poor pop, recommend coated stock or skip the UV

When uncertain, ask the customer one batched clarifying question rather than guessing. Customers prefer being asked over having their job redone.
"""


# ---------------------------------------------------------------------------
# Tool surface — Vertex AI FunctionDeclarations
# ---------------------------------------------------------------------------
#
# Hand-authored to mirror the docstrings on the underlying tool functions
# (servers/{parse_shoptalk,registry,mongodb,inspect_pdf,render_preview}/tools.py).
# Hand-authoring rather than fetching live from MCP keeps the schema
# Vertex-friendly: FastMCP's auto-generated inputSchema sometimes emits
# `anyOf`/$defs/$ref shapes that Vertex's FunctionDeclaration rejects.
# Per-tool descriptions stay concise; the heavy routing lift is in
# SYSTEM_PROMPT above.


_PARSE_SHOPTALK_DECL = FunctionDeclaration(
    name="parse_shoptalk",
    description=(
        "Parse a #lang shoptalk source program. Returns "
        "{ok: true, action_plan: <s-expression>} on success or "
        "{ok: false, error_class, message, exit_code} on failure. "
        "Field syntax: `<name>: <value>` separated by whitespace, fields "
        "end at newline. Job block: `job \"<name>\" { <fields> }`. "
        "Postcards (USPS bounds 3.5–6in × 3.5–4.25in) use type:postcard; "
        "non-USPS sizes (e.g. 4×9 push cards) use type:flat-card to skip "
        "dimensional validation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": (
                    "Full #lang shoptalk source program, beginning with "
                    "`#lang shoptalk`."
                ),
            },
        },
        "required": ["source"],
    },
)


_QUERY_STOCK_REGISTRY_DECL = FunctionDeclaration(
    name="query_stock_registry",
    description=(
        "Search Voom Group's stock inventory. Use this whenever the "
        "customer mentions a paper, stock, or substrate — even in fuzzy "
        "language ('something like 80# cover'). Pass language verbatim "
        "through criteria.text_search; the registry resolves it. Results "
        "include match_tier ∈ {exact, alias, ambiguous, name-substring, "
        "alias-substring, token-overlap}. Treat exact/alias as confident; "
        "ambiguous → ask the customer; substring/token-overlap → fuzzy "
        "suggestions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "description": (
                    "Search criteria. Keys: text_search (free text), "
                    "basis_weight_min/_max (numbers), "
                    "coating ('coated'|'uncoated'|'any'), "
                    "finish (e.g. 'gloss', 'matte', 'silk')."
                ),
                "properties": {
                    "text_search": {"type": "string"},
                    "basis_weight_min": {"type": "number"},
                    "basis_weight_max": {"type": "number"},
                    "coating": {"type": "string"},
                    "finish": {"type": "string"},
                },
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Max candidates to return. 1 for confident lookups, "
                    "3-5 to offer options. Default 3."
                ),
            },
        },
        "required": ["criteria"],
    },
)


_QUERY_PRESS_REGISTRY_DECL = FunctionDeclaration(
    name="query_press_registry",
    description=(
        "Search Voom Group's press inventory. Use when a press is named "
        "by alias/shortname ('Big Fuji', 'the Fuji'), or when filtering "
        "by format. Same match_tier semantics as query_stock_registry."
    ),
    parameters={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "description": (
                    "Search criteria. Keys: text_search (free text), "
                    "format ('sheet'|'wide-format'|'any')."
                ),
                "properties": {
                    "text_search": {"type": "string"},
                    "format": {"type": "string"},
                },
            },
            "limit": {
                "type": "integer",
                "description": "Max candidates to return. Default 3.",
            },
        },
        "required": ["criteria"],
    },
)


_LOOKUP_CUSTOMER_DECL = FunctionDeclaration(
    name="lookup_customer",
    description=(
        "Look up a customer by email (preferred) or name. Returns "
        "{ok: true, found: true, customer, recent_jobs} on hit, "
        "{ok: true, found: false} on miss. Call at the start of every "
        "conversation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Email (preferred) or name substring.",
            },
        },
        "required": ["query"],
    },
)


_CREATE_CUSTOMER_DECL = FunctionDeclaration(
    name="create_customer",
    description=(
        "Create a new customer record. Call ONLY after lookup_customer "
        "returned found:false. Returns {ok: true, customer_id} on "
        "success, {ok: false, error: 'duplicate_email'} on conflict."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Customer name."},
            "email": {"type": "string", "description": "Customer email (may be empty for walk-ins)."},
            "phone": {"type": "string", "description": "Customer phone (optional)."},
            "notes": {"type": "string", "description": "Shop relationship notes (optional)."},
        },
        "required": ["name", "email"],
    },
)


_UPDATE_JOB_STATUS_DECL = FunctionDeclaration(
    name="update_job_status",
    description=(
        "Stream a phase update to the CSR dashboard. Allowed phases: "
        "reading_message, checking_attachments, looking_up_customer, "
        "resolving_stocks, resolving_presses, checking_coatings, "
        "validating_dates, drafting_reply, validating_spec, "
        "ready_for_review, clarification_needed, human_review, done, "
        "escalated. Returns {ok: false, error: 'job_not_found'} if the "
        "job hasn't been persisted yet — call persist_job first for any "
        "child J-number beyond the first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "J-number, e.g. 'J123456-01'.",
            },
            "phase": {
                "type": "string",
                "description": "One of the allowed phases.",
            },
        },
        "required": ["job_id", "phase"],
    },
)


_APPEND_CONVERSATION_TURN_DECL = FunctionDeclaration(
    name="append_conversation_turn",
    description=(
        "Append a turn to the conversation log. Use role='agent' for "
        "your reasoning, role='user' for the customer's message, "
        "role='agent_to_customer' with status='draft' for messages the "
        "CSR should review before sending. Hard cap: 3 'agent_to_customer' "
        "draft turns total per parent message — Voomie escalates "
        "automatically beyond that."
    ),
    parameters={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "J-number."},
            "role": {
                "type": "string",
                "description": "'user', 'agent', or 'agent_to_customer'.",
            },
            "content": {"type": "string", "description": "Turn text."},
            "status": {
                "type": "string",
                "description": "'sent', 'draft', or 'pending_review'. Default 'sent'.",
            },
        },
        "required": ["job_id", "role", "content"],
    },
)


_PERSIST_JOB_DECL = FunctionDeclaration(
    name="persist_job",
    description=(
        "Write the final job record to MongoDB. Required fields: _id "
        "(J-number 'J######-##'), customer_id, status, phase, "
        "declaration_source, action_plan, attachments_metadata, "
        "out_of_scope_notes, due_date, rush. Side effect: updates "
        "customer.last_seen. Idempotent (replace_one upsert)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "job_record": {
                "type": "object",
                "description": (
                    "Full job record. _id must match ^J\\d{6}-\\d{2}$. "
                    "customer_id is the stringified ObjectId from "
                    "lookup_customer or create_customer. status ∈ "
                    "{drafting, ready_for_review, clarification_needed, "
                    "human_review, done, escalated}. phase is the "
                    "current human-readable phase. declaration_source "
                    "is the full #lang shoptalk source. action_plan is "
                    "the s-expression returned by parse_shoptalk. "
                    "attachments_metadata is a list of dicts. "
                    "out_of_scope_notes is a list of strings. due_date "
                    "is ISO 8601 or null. rush is boolean."
                ),
                "properties": {
                    "_id": {"type": "string"},
                    "parent_id": {"type": "string"},
                    "customer_id": {"type": "string"},
                    "status": {"type": "string"},
                    "phase": {"type": "string"},
                    "declaration_source": {"type": "string"},
                    "action_plan": {"type": "string"},
                    "attachments_metadata": {"type": "array", "items": {"type": "object"}},
                    "out_of_scope_notes": {"type": "array", "items": {"type": "string"}},
                    "due_date": {"type": "string"},
                    "rush": {"type": "boolean"},
                },
            },
        },
        "required": ["job_record"],
    },
)


_FLAG_FOR_HUMAN_DECL = FunctionDeclaration(
    name="flag_for_human",
    description=(
        "Surface the job in the CSR review queue with full context. "
        "Side effect: sets the job's phase to 'human_review'. Call when "
        "you cannot resolve an ambiguity within 3 customer-facing turns, "
        "when parse_shoptalk fails twice, or when a required field is "
        "structurally unrecoverable."
    ),
    parameters={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "J-number."},
            "reason": {"type": "string", "description": "Short reason code."},
            "context": {
                "type": "string",
                "description": (
                    "Detailed context the CSR will see; include all "
                    "relevant ambiguity, what you tried, and what's "
                    "missing."
                ),
            },
        },
        "required": ["job_id", "reason", "context"],
    },
)


_INSPECT_PDF_DECL = FunctionDeclaration(
    name="inspect_pdf",
    description=(
        "Read PDF metadata: trim size, page count, color space, fonts, "
        "bleed, file health. Returns {ok: true, ...} on success, or "
        "{ok: false, error_class: 'not_pdf'|'encrypted'|'corrupted'|"
        "'not_found'|'internal_error', message} on failure. For "
        "non-PDF files (JPG, PNG, AI, INDD), the tool returns "
        "error_class:'not_pdf' — your job is to ask the customer for a "
        "PDF; do not attempt conversion."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file on disk.",
            },
        },
        "required": ["file_path"],
    },
)


_RENDER_PREVIEW_DECL = FunctionDeclaration(
    name="render_preview",
    description=(
        "Render a shoptalk action plan to a preview PDF. Pass the "
        "action_plan field returned by parse_shoptalk verbatim; "
        "hand-edited plans will fail because the verifier expects "
        "parser-stamped fields. Use only when explicitly asked (the "
        "booklet preview demo beat)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action_plan": {
                "type": "string",
                "description": (
                    "S-expression text from parse_shoptalk's action_plan."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Optional output path. Empty string → temp dir."
                ),
            },
        },
        "required": ["action_plan"],
    },
)


TOOL_DECLARATIONS: list[FunctionDeclaration] = [
    _PARSE_SHOPTALK_DECL,
    _QUERY_STOCK_REGISTRY_DECL,
    _QUERY_PRESS_REGISTRY_DECL,
    _LOOKUP_CUSTOMER_DECL,
    _CREATE_CUSTOMER_DECL,
    _UPDATE_JOB_STATUS_DECL,
    _APPEND_CONVERSATION_TURN_DECL,
    _PERSIST_JOB_DECL,
    _FLAG_FOR_HUMAN_DECL,
    _INSPECT_PDF_DECL,
    _RENDER_PREVIEW_DECL,
]


# Canonical top-level parameter keys per tool, mirrored from each
# FunctionDeclaration above. Used for typo auto-correction in the dispatch
# path (Gemini Flash periodically mangles `job_id` → `joba_id` and similar;
# without correction every downstream tool call returns a validation error
# and the loop wedges).
_TOOL_PARAM_KEYS: dict[str, set[str]] = {
    "parse_shoptalk": {"source"},
    "query_stock_registry": {"criteria", "limit"},
    "query_press_registry": {"criteria", "limit"},
    "lookup_customer": {"query"},
    "create_customer": {"name", "email", "phone", "notes"},
    "update_job_status": {"job_id", "phase"},
    "append_conversation_turn": {"job_id", "role", "content", "status"},
    "persist_job": {"job_record"},
    "flag_for_human": {"job_id", "reason", "context"},
    "inspect_pdf": {"file_path"},
    "render_preview": {"action_plan", "output_path"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_j_number() -> str:
    """Return a parent J-number ('J######').

    Pattern: J + 6 digits, derived from unix-second mod 1_000_000 plus a
    small random nudge. 6-digit space gives ~1M unique IDs; collisions
    are vanishingly rare for demo cadence and re-runnable test suites.
    Children of this parent are formed by `child_j_number(parent, n)`.
    """
    seed = int(time.time()) % 1_000_000
    # Small random nudge avoids back-to-back parent collisions when two
    # process_message() calls land in the same second (test runs).
    nudge = random.randint(0, 99)
    n = (seed + nudge) % 1_000_000
    return f"J{n:06d}"


def child_j_number(parent: str, idx: int) -> str:
    """Return the idx-th child J-number under parent (J######-NN)."""
    return f"{parent}-{idx:02d}"


def _server_env() -> dict[str, str]:
    """Forward env vars the combined server's subprocesses need.

    Mirrors tests/test_voomie_server.py — MCPBridge does NOT auto-inherit
    custom env vars, so MONGODB_URI / SHOPTALK_REPO_PATH / RACKET_BIN /
    VOOMIE_DB have to be passed explicitly.
    """
    env: dict[str, str] = {}
    for key in ("SHOPTALK_REPO_PATH", "RACKET_BIN", "MONGODB_URI", "VOOMIE_DB"):
        if key in os.environ:
            env[key] = os.environ[key]
    # PATH/HOME are needed by the subprocess (Python interpreter, etc.).
    for key in ("PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _build_initial_message(
    customer_query: str,
    message_text: str,
    parent: str,
    attachments: list[str] | None,
) -> str:
    """Assemble the first message Gemini sees.

    Header lines anchor Gemini on the customer identifier (so it knows
    which email/name to lookup_customer with), the parent J-number (so
    it knows what to use as J######-01 etc.), and the attachment paths
    (so it knows what to inspect_pdf). The customer's raw text follows
    verbatim — we don't sanitize, the agent reads what the customer wrote.
    """
    lines = [
        f"[CUSTOMER QUERY: {customer_query}]",
        f"[PARENT JOB ID: {parent}]",
        f"[FIRST CHILD JOB ID: {child_j_number(parent, 1)}]",
    ]
    if attachments:
        lines.append(f"[ATTACHMENTS: {', '.join(attachments)}]")
    else:
        lines.append("[ATTACHMENTS: (none)]")
    lines.append("")
    lines.append(message_text)
    return "\n".join(lines)


def _stub_job_record(
    job_id: str,
    parent: str | None = None,
) -> dict[str, Any]:
    """Build a placeholder job record so update_job_status can write to it.

    SPEC.md §Multi-step mission step 1 requires creating job records
    immediately. The stub satisfies persist_job's J-number validation
    and makes update_job_status succeed; the agent's eventual persist_job
    call upserts the real fields.
    """
    return {
        "_id": job_id,
        "parent_id": parent,
        "customer_id": None,
        "status": "drafting",
        "phase": "reading_message",
        "declaration_source": "",
        "action_plan": "",
        "attachments_metadata": [],
        "out_of_scope_notes": [],
        "due_date": None,
        "rush": False,
    }


def _log(msg: str) -> None:
    """Print a timestamped line to stderr.

    Console output is the demo's debugging surface — be generous with it.
    All agent logging goes to stderr so the CLI's stdout JSON stays clean.
    """
    print(f"[agent] {msg}", file=sys.stderr, flush=True)


def _safe_args_repr(args: dict[str, Any], max_len: int = 240) -> str:
    """Render Gemini's tool args for the log without dumping a full PDF blob."""
    try:
        rendered = json.dumps(args, default=str)
    except Exception:
        rendered = repr(args)
    if len(rendered) > max_len:
        return rendered[:max_len] + f"...[truncated, total {len(rendered)} chars]"
    return rendered


def _safe_resp_repr(resp: dict[str, Any], max_len: int = 240) -> str:
    """Render a tool response for the log, truncated."""
    try:
        rendered = json.dumps(resp, default=str)
    except Exception:
        rendered = repr(resp)
    if len(rendered) > max_len:
        return rendered[:max_len] + f"...[truncated, total {len(rendered)} chars]"
    return rendered


def _autocorrect_param_typos(
    tool_name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Map close-spelling typos in arg keys back to the tool's canonical
    parameter names.

    Why: Gemini Flash sporadically mangles parameter names ('joba_id'
    instead of 'job_id'). Without correction, every affected tool call
    returns a validation error from FastMCP, the bridge surfaces it back
    to Gemini, and Gemini happily retries with the same typo — the loop
    burns turns without progressing. Catching the typo at dispatch time
    fixes the symptom locally; the warning log lets us see when it fires
    so we know how often the model is misbehaving.

    Algorithm: for each key not in the tool's known set, find the closest
    valid key by difflib.SequenceMatcher ratio. Substitute when ratio ≥
    PARAM_TYPO_SIMILARITY_THRESHOLD (0.8) and the canonical key isn't
    already populated. Unknown keys with no close match pass through
    unchanged so the bridge still surfaces a clear validation error.

    Returns:
      (corrected_args, num_corrections_applied)
    """
    valid_keys = _TOOL_PARAM_KEYS.get(tool_name)
    if not valid_keys:
        return args, 0

    corrected: dict[str, Any] = {}
    corrections_applied = 0
    for key, value in args.items():
        if key in valid_keys:
            corrected[key] = value
            continue

        best_match: str | None = None
        best_ratio: float = 0.0
        for candidate in valid_keys:
            ratio = difflib.SequenceMatcher(None, key, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

        if (
            best_match is not None
            and best_ratio >= PARAM_TYPO_SIMILARITY_THRESHOLD
            and best_match not in corrected
            and best_match not in args
        ):
            _log(
                f"auto-corrected parameter typo: {key} → {best_match} "
                f"(tool: {tool_name}, similarity={best_ratio:.2f})"
            )
            corrected[best_match] = value
            corrections_applied += 1
        else:
            # Pass the unknown key through unchanged. The MCP tool will
            # raise a clear validation error that Gemini can react to.
            corrected[key] = value

    return corrected, corrections_applied


def _detect_malformed_function_call(response: Any) -> tuple[bool, str]:
    """Detect Vertex Finish reason 9 (MALFORMED_FUNCTION_CALL).

    With response_validation=False, chat.send_message no longer raises on
    a malformed turn — instead it returns a response whose first candidate
    has finish_reason=9 and finish_message containing the offending Python-
    like text (e.g. `default_api.update_job_status(job_id="X")`). We
    detect that here so the caller can synthesize a corrective text turn
    instead of trying to dispatch a phantom tool call.

    We deliberately do NOT parse-and-execute the malformed Python — that's
    a sandbox hazard and unnecessary if Gemini retries correctly when
    asked.

    Returns:
      (is_malformed, snippet_for_log)  — snippet is empty when not malformed.
    """
    try:
        cand = response.candidates[0]
    except Exception:
        return False, ""

    fr = getattr(cand, "finish_reason", None)
    fr_val: int | None = None
    # FinishReason may be an IntEnum, a proto enum value, or a raw int
    # depending on SDK version; try the common shapes.
    val_attr = getattr(fr, "value", None)
    if isinstance(val_attr, int):
        fr_val = val_attr
    elif isinstance(fr, int):
        fr_val = fr
    else:
        try:
            fr_val = int(fr)  # last-ditch coerce
        except Exception:
            fr_val = None

    if fr_val != 9:
        return False, ""

    # The malformed Python text usually shows up in finish_message; if
    # not, fall back to the first text part.
    snippet = getattr(cand, "finish_message", "") or ""
    if not snippet:
        try:
            for part in cand.content.parts:
                text_part = getattr(part, "text", None)
                if text_part:
                    snippet = text_part
                    break
        except Exception:
            pass
    return True, (snippet or "")[:300]


def _final_state(parent: str) -> dict[str, Any]:
    """Determine the agent's terminal state from MongoDB.

    Imports the same tool module the seeder uses (servers.mongodb.tools)
    to read jobs and flags by parent prefix. Final status priority:
    escalated > human_review > clarification_needed > ready_for_review.
    Falls back to 'human_review' when no job documents match the parent
    (something went very wrong before the stub was written).
    """
    try:
        from servers.mongodb import tools as mdb
    except Exception as e:  # pragma: no cover — only fires on import-time breakage
        return {
            "child_job_ids": [],
            "final_status": "human_review",
            "declarations_produced": 0,
            "flags_raised": 0,
            "_state_error": f"mongodb tools import failed: {e}",
        }

    db = mdb._get_db()
    if db is None:
        return {
            "child_job_ids": [],
            "final_status": "human_review",
            "declarations_produced": 0,
            "flags_raised": 0,
            "_state_error": "mongodb_unavailable",
        }

    # _id matches ^{parent}-\d{2}$
    pattern = {"$regex": f"^{parent}-\\d{{2}}$"}
    jobs = list(db["jobs"].find({"_id": pattern}))
    flag_count = db["flags"].count_documents({"job_id": pattern})

    child_ids = sorted(j["_id"] for j in jobs)

    declarations_produced = sum(
        1 for j in jobs if (j.get("declaration_source") or "").strip()
    )

    # Phase priority — matches the user's allowed final_status set.
    priority = ["escalated", "human_review", "clarification_needed", "ready_for_review"]
    final_status = None
    for tier in priority:
        if any(j.get("phase") == tier for j in jobs):
            final_status = tier
            break

    # If no job has a recognized final phase, infer from intermediates:
    # if a flag exists, treat as human_review; else clarification_needed
    # (the agent stopped mid-flight without committing).
    if final_status is None:
        if flag_count > 0:
            final_status = "human_review"
        else:
            final_status = "clarification_needed"

    return {
        "child_job_ids": child_ids,
        "final_status": final_status,
        "declarations_produced": declarations_produced,
        "flags_raised": flag_count,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def process_message(
    customer_query: str,
    message_text: str,
    attachments: list[str] | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Run Voomie on a single customer message end-to-end.

    Steps (mirror SPEC.md §Multi-step mission, but with operational
    guarantees on top — see module docstring for context):

      1. Generate a parent J-number and pre-create the J######-01 stub.
      2. Spawn the combined MCP server.
      3. Initialize Vertex AI Gemini with SYSTEM_PROMPT and the 11
         FunctionDeclarations.
      4. Send the assembled initial message to Gemini.
      5. Loop: dispatch each function_call through the bridge, send
         function-response Parts back, until Gemini emits text-only or
         we hit MAX_AGENT_TURNS.
      6. Track customer-facing draft turns; force escalation at the
         3-turn cap.
      7. Query MongoDB for terminal state, return the final dict.

    Returns:
      {
        "ok": True,
        "parent_job_id": "J######",
        "child_job_ids": [...],
        "final_status": "ready_for_review" | "clarification_needed" |
                        "human_review" | "escalated",
        "declarations_produced": <int>,
        "flags_raised": <int>,
        "elapsed_seconds": <float>,
      }

    On unhandled exception:
      {"ok": False, "error": <str>, "parent_job_id": <str>}
    """
    started_at = time.time()
    parent = generate_j_number()
    first_child = child_j_number(parent, 1)
    today_iso = datetime.utcnow().date().isoformat()
    effective_model = model_name or DEFAULT_MODEL_NAME

    # Run-level counters surfaced in the result for the demo report.
    typo_corrections_applied = 0
    malformed_turns_total = 0
    malformed_turns_recovered = 0

    _log(f"start parent={parent} first_child={first_child} customer={customer_query!r}")
    _log(f"message_length={len(message_text)} attachments={attachments or []}")

    try:
        _log(
            f"vertex init project={GCP_PROJECT_ID} region={GCP_REGION} "
            f"model={effective_model}"
        )
        vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
        model = GenerativeModel(
            effective_model,
            system_instruction=[SYSTEM_PROMPT.format(today_iso=today_iso)],
            tools=[Tool(function_declarations=TOOL_DECLARATIONS)],
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"vertex_init_failed: {e}",
            "parent_job_id": parent,
        }

    try:
        with MCPBridge(
            server_command=sys.executable,
            server_args=[str(SERVER_PATH)],
            env=_server_env(),
        ) as bridge:
            advertised = bridge.list_tools()
            _log(f"mcp tools advertised: {sorted(advertised)}")
            if len(advertised) != 11:
                _log(f"WARNING: expected 11 tools, got {len(advertised)}")

            # Pre-create the first child job stub so update_job_status
            # works from turn 1. This is the SPEC.md §step 1 "create job
            # records immediately" guarantee.
            stub_resp = bridge.call_tool(
                "persist_job", {"job_record": _stub_job_record(first_child, parent)}
            )
            _log(f"prestub persist_job → {_safe_resp_repr(stub_resp.get('structured') or {})}")

            # Initial dashboard ping + log the user's message into the
            # conversation. Gemini will likely repeat append_conversation_turn
            # for the user message, but we'd rather have it appear twice in
            # the log than not at all if Gemini skips this step.
            bridge.call_tool(
                "update_job_status",
                {"job_id": first_child, "phase": "reading_message"},
            )
            bridge.call_tool(
                "append_conversation_turn",
                {
                    "job_id": first_child,
                    "role": "user",
                    "content": message_text,
                    "status": "sent",
                },
            )

            initial = _build_initial_message(
                customer_query, message_text, parent, attachments
            )

            # response_validation=False is per Vertex's own error guidance:
            # without it, a single Finish-reason-9 (malformed function call)
            # turn raises out of chat.send_message and kills the loop. With
            # it, the response comes back and we can inspect finish_reason
            # ourselves and synthesize a corrective text turn (handled below).
            chat = model.start_chat(response_validation=False)
            try:
                response = chat.send_message(initial)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"initial_send_failed: {e}",
                    "parent_job_id": parent,
                }

            customer_facing_turns = 0
            forced_escalated = False
            consecutive_malformed = 0
            agent_turn = 0

            while agent_turn < MAX_AGENT_TURNS:
                agent_turn += 1
                try:
                    fcs = (
                        list(response.candidates[0].function_calls)
                        if response.candidates
                        else []
                    )
                except Exception as e:
                    _log(f"failed to read function_calls: {e}")
                    fcs = []

                if not fcs:
                    # Either Gemini emitted real final text OR Vertex
                    # marked the response Finish-reason-9 (malformed call).
                    # The two need very different handling.
                    is_malformed, snippet = _detect_malformed_function_call(response)
                    if is_malformed:
                        malformed_turns_total += 1
                        consecutive_malformed += 1
                        _log(
                            f"turn {agent_turn}: Vertex Finish reason 9 "
                            f"(malformed function call), consecutive "
                            f"{consecutive_malformed}/{MAX_CONSECUTIVE_MALFORMED_TURNS}. "
                            f"Snippet: {snippet!r}"
                        )
                        if consecutive_malformed >= MAX_CONSECUTIVE_MALFORMED_TURNS:
                            _log(
                                "consecutive malformed cap reached — "
                                "force-escalating to human"
                            )
                            forced_escalated = True
                            for child_id in (
                                first_child,
                                child_j_number(parent, 2),
                            ):
                                bridge.call_tool(
                                    "flag_for_human",
                                    {
                                        "job_id": child_id,
                                        "reason": "malformed_function_call_loop",
                                        "context": (
                                            f"Gemini emitted Python source "
                                            f"instead of structured function "
                                            f"calls "
                                            f"{MAX_CONSECUTIVE_MALFORMED_TURNS} "
                                            f"turns in a row on parent "
                                            f"{parent}. Escalating to CSR review."
                                        ),
                                    },
                                )
                                bridge.call_tool(
                                    "update_job_status",
                                    {"job_id": child_id, "phase": "escalated"},
                                )
                            break
                        try:
                            response = chat.send_message(
                                "Your previous response was rejected: it "
                                "contained Python source code (e.g. "
                                "`default_api.update_job_status(...)`) "
                                "inside a text part instead of structured "
                                "function calls. You MUST emit tool calls "
                                "via the function-calling API — do NOT "
                                "write Python code. Please retry the "
                                "previous step using the proper structured "
                                "tool-calling format, one tool call at a "
                                "time."
                            )
                            malformed_turns_recovered += 1
                        except Exception as e:
                            _log(f"corrective resend failed: {e}")
                            return {
                                "ok": False,
                                "error": f"chat_corrective_failed: {e}",
                                "parent_job_id": parent,
                            }
                        continue

                    _log(f"turn {agent_turn}: no function_calls — Gemini emitted final text")
                    break

                # Reset the malformed streak on any well-formed turn.
                consecutive_malformed = 0

                _log(f"turn {agent_turn}: Gemini requested {len(fcs)} tool call(s)")
                response_parts: list[Part] = []
                for fc in fcs:
                    raw_args = dict(fc.args) if fc.args else {}
                    args, num_corrections = _autocorrect_param_typos(
                        fc.name, raw_args
                    )
                    typo_corrections_applied += num_corrections
                    _log(f"  → {fc.name}({_safe_args_repr(args)})")

                    # Track customer-facing turns BEFORE dispatch — even
                    # if the dispatch fails, the agent's intent counted
                    # toward the cap.
                    if (
                        fc.name == "append_conversation_turn"
                        and args.get("role") == "agent_to_customer"
                        and args.get("status") == "draft"
                    ):
                        customer_facing_turns += 1
                        _log(
                            f"  customer-facing draft turn {customer_facing_turns} "
                            f"of {CUSTOMER_FACING_TURN_CAP}"
                        )

                    try:
                        result = bridge.call_tool(fc.name, args)
                        tool_resp = result["structured"] or {"raw": result["text"]}
                    except Exception as e:
                        tool_resp = {"ok": False, "error": f"bridge_dispatch_failed: {e}"}
                    _log(f"  ← {_safe_resp_repr(tool_resp)}")

                    response_parts.append(
                        Part.from_function_response(name=fc.name, response=tool_resp)
                    )

                    # Hard 3-turn cap. Force-escalate as soon as the cap
                    # is hit; do not give Gemini another opportunity to
                    # draft a fourth customer-facing turn.
                    if (
                        customer_facing_turns >= CUSTOMER_FACING_TURN_CAP
                        and not forced_escalated
                    ):
                        forced_escalated = True
                        _log(
                            f"3-turn customer-facing cap reached — forcing escalation"
                        )
                        # Best-effort flag + phase set for both potential
                        # children. If only one exists, the second call
                        # is a no-op error.
                        for child_id in (first_child, child_j_number(parent, 2)):
                            bridge.call_tool(
                                "flag_for_human",
                                {
                                    "job_id": child_id,
                                    "reason": "three_turn_cap_exceeded",
                                    "context": (
                                        f"Voomie hit the 3-turn customer-facing cap "
                                        f"on parent {parent} without resolving the "
                                        f"declaration. Per SPEC.md §Conversation flow, "
                                        f"escalating to CSR review."
                                    ),
                                },
                            )
                            bridge.call_tool(
                                "update_job_status",
                                {"job_id": child_id, "phase": "escalated"},
                            )
                        break  # break inner fc loop

                if forced_escalated:
                    break  # break outer turn loop

                try:
                    response = chat.send_message(response_parts)
                except Exception as e:
                    _log(f"chat.send_message failed mid-loop: {e}")
                    return {
                        "ok": False,
                        "error": f"chat_mid_loop_failed: {e}",
                        "parent_job_id": parent,
                    }

            if agent_turn >= MAX_AGENT_TURNS and not forced_escalated:
                _log(
                    f"hit MAX_AGENT_TURNS ({MAX_AGENT_TURNS}) without final text — "
                    f"flagging for human"
                )
                bridge.call_tool(
                    "flag_for_human",
                    {
                        "job_id": first_child,
                        "reason": "max_agent_turns_exceeded",
                        "context": (
                            f"Gemini issued more than {MAX_AGENT_TURNS} tool turns "
                            f"without producing a final text response. Likely a "
                            f"reasoning loop; CSR review required."
                        ),
                    },
                )

            # Best-effort: log Gemini's final text to the conversation
            # so the CSR sees it on the dashboard. Skip when we exited
            # via forced escalation — `response` may be a malformed turn
            # whose .text would surface raw Python source instead of a
            # CSR-readable summary.
            final_text: str | None = None
            if not forced_escalated:
                try:
                    final_text = response.text  # may raise if the response is empty/blocked
                except Exception:
                    final_text = None
            if final_text:
                _log(f"Gemini final text: {final_text[:200]}...")
                bridge.call_tool(
                    "append_conversation_turn",
                    {
                        "job_id": first_child,
                        "role": "agent",
                        "content": final_text,
                        "status": "sent",
                    },
                )

    except Exception as e:
        _log(f"unhandled exception: {e}")
        return {
            "ok": False,
            "error": f"unhandled_exception: {e}",
            "parent_job_id": parent,
        }

    # Bridge is now torn down; query MongoDB directly for terminal state.
    state = _final_state(parent)
    elapsed = time.time() - started_at
    _log(
        f"done parent={parent} status={state['final_status']} "
        f"children={len(state['child_job_ids'])} "
        f"declarations={state['declarations_produced']} "
        f"flags={state['flags_raised']} "
        f"typo_corrections={typo_corrections_applied} "
        f"malformed_turns={malformed_turns_total} "
        f"malformed_recovered={malformed_turns_recovered} "
        f"model={effective_model} elapsed={elapsed:.1f}s"
    )

    return {
        "ok": True,
        "parent_job_id": parent,
        "child_job_ids": state["child_job_ids"],
        "final_status": state["final_status"],
        "declarations_produced": state["declarations_produced"],
        "flags_raised": state["flags_raised"],
        "typo_corrections_applied": typo_corrections_applied,
        "malformed_turns_total": malformed_turns_total,
        "malformed_turns_recovered": malformed_turns_recovered,
        "model": effective_model,
        "elapsed_seconds": round(elapsed, 2),
    }
