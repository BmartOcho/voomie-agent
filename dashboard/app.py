"""
dashboard/app.py — Voomie CSR dashboard.

The demo's primary visual surface. A single-page Streamlit app with three
panes:

  1. New customer message receiver (top, paste-in)
  2. Active job queue (middle, grouped by parent J-number)
  3. Selected job detail (bottom, conversation + metadata + draft replies)

The dashboard reads MongoDB directly via pymongo (this is internal tooling,
same pattern as scripts/seed_db.py). Tool functions from
servers/mongodb/tools.py handle the actual queries and the Send-to-Customer
mutation goes straight through pymongo since we already hold a client.

Spawning the agent: when the CSR clicks "Process with Voomie", we
subprocess.Popen `python -m voomie.cli ...` with stdout/stderr piped to
log files under /tmp/voomie-runs/<run-id>/. We do NOT block — the
auto-refresh loop (1s polling) picks up the new job in MongoDB as the
agent streams phase updates.

Run: streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymongo
import streamlit as st
from bson import ObjectId
from streamlit_autorefresh import st_autorefresh

# Make project imports work regardless of how Streamlit is invoked.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.styles import (  # noqa: E402
    PHASE_BUCKETS,
    STYLES,
    customer_badge,
    phase_pill,
    role_badge,
    turn_status_badge,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MONGODB_URI = os.environ.get("MONGODB_URI", "")
DB_NAME = os.environ.get("VOOMIE_DB", "voomie")
UPLOAD_ROOT = Path("/tmp/voomie-uploads")
RUN_LOG_ROOT = Path("/tmp/voomie-runs")
DEFAULT_REFRESH_MS = 1000  # 1-second polling per spec.

st.set_page_config(
    page_title="Voomie — CSR Dashboard",
    page_icon="🖨️",
    layout="wide",
)
st.markdown(STYLES, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _mongo_client() -> pymongo.MongoClient | None:
    if not MONGODB_URI:
        return None
    try:
        client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        return client
    except Exception:
        return None


def _db():
    client = _mongo_client()
    return client[DB_NAME] if client is not None else None


def check_connection() -> tuple[bool, str | None]:
    """Return (ok, error_message)."""
    if not MONGODB_URI:
        return False, "MONGODB_URI is not set in this shell."
    try:
        client = _mongo_client()
        if client is None:
            return False, "Could not initialize MongoClient."
        client.admin.command("ping")
        return True, None
    except Exception as e:  # noqa: BLE001 — surface raw error to the operator
        return False, str(e)


def fetch_all_jobs() -> list[dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    try:
        return list(db["jobs"].find({}))
    except Exception:
        return []


def fetch_customer(customer_id: Any) -> dict[str, Any] | None:
    db = _db()
    if db is None or customer_id is None:
        return None
    try:
        cid = customer_id
        if isinstance(cid, str):
            try:
                cid = ObjectId(cid)
            except Exception:
                pass
        return db["customers"].find_one({"_id": cid})
    except Exception:
        return None


def count_customer_jobs(customer_id: Any) -> int:
    db = _db()
    if db is None or customer_id is None:
        return 0
    try:
        # customer_id may be stored as either a stringified ObjectId or an
        # ObjectId depending on writer; query both forms for robustness.
        cid_str = str(customer_id)
        n = db["jobs"].count_documents({"customer_id": cid_str})
        if n == 0 and isinstance(customer_id, ObjectId):
            n = db["jobs"].count_documents({"customer_id": customer_id})
        return n
    except Exception:
        return 0


def fetch_conversation(job_id: str) -> list[dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    try:
        doc = db["conversations"].find_one({"job_id": job_id})
        if not doc:
            return []
        return list(doc.get("messages") or [])
    except Exception:
        return []


def fetch_flags(job_id: str) -> list[dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    try:
        return list(db["flags"].find({"job_id": job_id}))
    except Exception:
        return []


def mark_draft_sent(job_id: str, message_index: int) -> bool:
    """Toggle a single draft turn → status='sent' inside conversations.messages."""
    db = _db()
    if db is None:
        return False
    try:
        result = db["conversations"].update_one(
            {"job_id": job_id},
            {"$set": {f"messages.{message_index}.status": "sent"}},
        )
        return result.modified_count > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------


def _to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.rstrip("Z"))
        except Exception:
            return None
    return None


def _humanize_age(when: datetime | None) -> str:
    if when is None:
        return "—"
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    delta = datetime.utcnow() - when
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _job_summary(job: dict[str, Any]) -> str:
    """One-line summary derived from declaration_source, or 'Drafting…'."""
    decl = (job.get("declaration_source") or "").strip()
    if not decl:
        return "Drafting…"
    # Pluck a few human-friendly fields. The declaration is a shoptalk DSL
    # block — extract type, finish-size, quantity, stock with simple substring
    # parsing. Anything fancier and we'd be re-implementing the parser.
    pieces: list[str] = []
    for key in ("type", "finish-size", "quantity", "stock"):
        marker = f"{key}:"
        idx = decl.find(marker)
        if idx == -1:
            continue
        rest = decl[idx + len(marker):].splitlines()[0].strip()
        # Trim trailing comments / braces.
        for cut in ("#", "}", "{"):
            if cut in rest:
                rest = rest.split(cut, 1)[0].strip()
        if rest:
            pieces.append(rest)
    if not pieces:
        # Fallback: first non-`#lang` line.
        for line in decl.splitlines():
            line = line.strip()
            if line and not line.startswith("#lang") and not line.startswith("job"):
                return line[:80]
        return "Drafting…"
    return ", ".join(pieces)


def _group_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster jobs by parent J-number.

    parent_id is the link for sibling children. For seeded standalone jobs
    where parent_id is None, the job is its own group (group_key = _id).
    Each group bundles {parent, children, max_updated, customer_id}.
    """
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for j in jobs:
        parent = j.get("parent_id") or j.get("_id")
        by_parent[parent].append(j)

    groups: list[dict[str, Any]] = []
    for parent, children in by_parent.items():
        children_sorted = sorted(
            children, key=lambda c: c.get("_id") or ""
        )
        max_updated = max(
            (_to_dt(c.get("updated_at")) or _to_dt(c.get("created_at")) or datetime.min)
            for c in children_sorted
        )
        # Pick the customer_id from any child (they should all match for true
        # siblings). For standalone seeded jobs, this is just the job's owner.
        customer_id = next(
            (c.get("customer_id") for c in children_sorted if c.get("customer_id")),
            None,
        )
        groups.append({
            "parent": parent,
            "children": children_sorted,
            "max_updated": max_updated,
            "customer_id": customer_id,
        })

    groups.sort(key=lambda g: g["max_updated"], reverse=True)
    return groups


def _passes_filter(job: dict[str, Any], bucket: str) -> bool:
    if bucket == "All":
        return True
    allowed = PHASE_BUCKETS.get(bucket, frozenset())
    return job.get("phase") in allowed


# ---------------------------------------------------------------------------
# Agent subprocess spawn
# ---------------------------------------------------------------------------


def spawn_agent_run(
    customer: str,
    message: str,
    attachments: list[Path] | None = None,
) -> tuple[str, Path]:
    """Spawn the Voomie agent in the background. Returns (run_id, log_dir).

    Non-blocking: we return as soon as Popen has spawned the child. The
    dashboard's auto-refresh picks up MongoDB writes as the agent streams
    phase updates.
    """
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    log_dir = RUN_LOG_ROOT / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "voomie.cli",
        "--customer", customer,
        "--message", message,
    ]
    if attachments:
        cmd.append("--attachments")
        cmd.extend(str(p) for p in attachments)

    stdout_log = (log_dir / "stdout.log").open("wb")
    stderr_log = (log_dir / "stderr.log").open("wb")

    # Inherit the parent process env so MONGODB_URI / GCP_PROJECT_ID flow
    # through. cwd=REPO_ROOT so `python -m voomie.cli` resolves correctly.
    subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        stdout=stdout_log,
        stderr=stderr_log,
        start_new_session=True,
    )
    return run_id, log_dir


def save_uploaded_pdfs(uploaded_files: list[Any], run_id: str) -> list[Path]:
    """Persist uploaded files to /tmp/voomie-uploads/<run-id>/ and return paths."""
    if not uploaded_files:
        return []
    dest_dir = UPLOAD_ROOT / run_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for f in uploaded_files:
        target = dest_dir / f.name
        target.write_bytes(f.getbuffer())
        paths.append(target)
    return paths


# ---------------------------------------------------------------------------
# Render: header
# ---------------------------------------------------------------------------


def render_header(connection_ok: bool, connection_err: str | None) -> None:
    left, right = st.columns([5, 1])
    with left:
        st.markdown(
            "<p class='voomie-wordmark'>Voomie</p>"
            "<p class='voomie-subtitle'>AI prepress assistant for Voom Group</p>",
            unsafe_allow_html=True,
        )
    with right:
        if connection_ok:
            st.markdown(
                "<div style='text-align:right;margin-top:14px;'>"
                "<span class='connection-pill' title='MongoDB ping ok'>"
                "<span class='connection-dot dot-ok'></span>MongoDB</span></div>",
                unsafe_allow_html=True,
            )
        else:
            tooltip = (connection_err or "no connection").replace('"', "'")
            st.markdown(
                f"<div style='text-align:right;margin-top:14px;'>"
                f"<span class='connection-pill' title=\"{tooltip}\">"
                f"<span class='connection-dot dot-fail'></span>MongoDB</span></div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Render: pane 1 (paste-in)
# ---------------------------------------------------------------------------


def render_paste_in_pane() -> None:
    with st.container(border=True):
        st.markdown("### 📨 New Customer Message")

        # Bump the form key on submit so widgets reset cleanly.
        form_nonce = st.session_state.setdefault("paste_form_nonce", 0)

        with st.form(key=f"paste_form_{form_nonce}", clear_on_submit=False):
            col_a, col_b = st.columns([1, 2])
            with col_a:
                customer = st.text_input(
                    "Customer email or name",
                    placeholder="chris@blastmailco.com",
                    key=f"customer_{form_nonce}",
                )
            with col_b:
                message = st.text_area(
                    "Message body",
                    placeholder="Paste customer email or chat message here…",
                    height=120,
                    key=f"message_{form_nonce}",
                )
            uploads = st.file_uploader(
                "Attachments (optional)",
                type=["pdf"],
                accept_multiple_files=True,
                key=f"uploads_{form_nonce}",
            )
            submit = st.form_submit_button(
                "🚀 Process with Voomie", type="primary", use_container_width=False,
            )

        st.caption(
            "Voomie can also be triggered from email, web-to-print portals, or "
            "our MIS — this is the manual paste-in for the demo."
        )

        if submit:
            if not customer.strip() or not message.strip():
                st.warning("Customer and message are both required.")
            else:
                run_id, _ = spawn_agent_run(
                    customer=customer.strip(),
                    message=message.strip(),
                    attachments=save_uploaded_pdfs(uploads, "tmp"),
                )
                # Replace the temp upload dir name with the real run id.
                tmp_dir = UPLOAD_ROOT / "tmp"
                final_dir = UPLOAD_ROOT / run_id
                if tmp_dir.exists():
                    tmp_dir.rename(final_dir)
                st.toast(
                    "Voomie is processing — watch the queue below.", icon="🚀",
                )
                st.session_state["paste_form_nonce"] = form_nonce + 1
                st.session_state["last_run_id"] = run_id
                st.rerun()


# ---------------------------------------------------------------------------
# Render: pane 2 (queue)
# ---------------------------------------------------------------------------


def render_queue_pane(jobs: list[dict[str, Any]]) -> None:
    st.markdown("### 📋 Active Job Queue")
    bucket = st.selectbox(
        "Filter",
        ["All", "In Progress", "Awaiting Review", "Human Review", "Done"],
        key="queue_filter",
        label_visibility="collapsed",
    )

    if not jobs:
        st.markdown(
            "<div class='empty-state'>"
            "No jobs yet. Paste a customer message above to see Voomie work."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    # Filter at the job level then re-group so a parent stays even if only
    # one of its children matches.
    filtered_jobs = [j for j in jobs if _passes_filter(j, bucket)]
    if not filtered_jobs:
        st.markdown(
            f"<div class='empty-state'>No jobs match filter “{bucket}”.</div>",
            unsafe_allow_html=True,
        )
        return

    groups = _group_jobs(filtered_jobs)

    selected = st.session_state.get("selected_job_id")

    for grp in groups:
        parent = grp["parent"]
        children = grp["children"]
        customer = fetch_customer(grp["customer_id"]) if grp["customer_id"] else None

        # Decide customer badge.
        n_customer_jobs = count_customer_jobs(grp["customer_id"])
        if customer and "walkin" in (customer.get("email") or "").lower():
            badge = "WALK-IN"
        elif n_customer_jobs > 1:
            badge = "RETURNING"
        else:
            badge = "NEW"

        cust_label = (customer or {}).get("name") or "Unknown customer"

        with st.container(border=True):
            st.markdown(
                "<div class='job-group-header'>"
                f"<div><span class='job-group-title'>📂 {parent}</span>"
                f"<span class='job-group-customer'>{cust_label}</span>"
                f"{customer_badge(badge)}</div>"
                f"<div class='job-group-time'>updated "
                f"{_humanize_age(grp['max_updated'])}</div>"
                "</div>",
                unsafe_allow_html=True,
            )

            for child in children:
                jid = child.get("_id", "")
                phase = child.get("phase") or "unknown"
                summary = _job_summary(child)
                flags = fetch_flags(jid)
                flag_html = (
                    f"<span class='flag-badge'>⚑ {len(flags)}</span>"
                    if flags else ""
                )

                row_l, row_r = st.columns([5, 1])
                with row_l:
                    st.markdown(
                        "<div class='child-row'>"
                        f"<span class='child-id'>{jid}</span>"
                        f"{phase_pill(phase)}"
                        f"<span class='child-summary'>{summary}</span>"
                        f"{flag_html}"
                        "</div>",
                        unsafe_allow_html=True,
                    )
                with row_r:
                    is_selected = (jid == selected)
                    label = "Selected ✓" if is_selected else "View"
                    if st.button(
                        label,
                        key=f"select_{jid}",
                        type="secondary" if not is_selected else "primary",
                        use_container_width=True,
                    ):
                        st.session_state["selected_job_id"] = jid
                        st.rerun()


# ---------------------------------------------------------------------------
# Render: pane 3 (detail)
# ---------------------------------------------------------------------------


def _render_code_block(label: str, body: str) -> None:
    body = (body or "").strip()
    if not body:
        return
    with st.expander(label, expanded=False):
        st.code(body, language=None)


def render_detail_pane(jobs: list[dict[str, Any]]) -> None:
    selected = st.session_state.get("selected_job_id")
    st.markdown("### 🔎 Job Detail")
    if not selected:
        st.markdown(
            "<div class='empty-state'>"
            "Select a job from the queue above to inspect its conversation, "
            "declaration, and any pending draft replies."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    job = next((j for j in jobs if j.get("_id") == selected), None)
    if job is None:
        st.warning(f"Job {selected} no longer exists.")
        st.session_state["selected_job_id"] = None
        return

    customer = fetch_customer(job.get("customer_id"))
    n_customer_jobs = count_customer_jobs(job.get("customer_id"))
    flags = fetch_flags(selected)

    # ----- Customer + metadata header ------------------------------------
    col_cust, col_meta = st.columns([1, 1])
    with col_cust:
        st.markdown("<div class='detail-block-label'>Customer</div>", unsafe_allow_html=True)
        if customer:
            st.markdown(
                f"<p class='detail-customer-name'>{customer.get('name', '—')}</p>"
                f"<p class='detail-customer-meta'>📧 {customer.get('email') or '—'}"
                f"  ·  📞 {customer.get('phone') or '—'}</p>"
                f"<p class='detail-customer-meta'>Recent jobs: {n_customer_jobs}</p>",
                unsafe_allow_html=True,
            )
            notes = (customer.get("shop_relationship_notes") or "").strip()
            if notes:
                st.markdown(
                    f"<div class='detail-notes'>📝 {notes}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                "<p class='detail-customer-meta'>No customer record linked.</p>",
                unsafe_allow_html=True,
            )

    with col_meta:
        st.markdown("<div class='detail-block-label'>Job</div>", unsafe_allow_html=True)
        rush_label = "🚨 RUSH" if job.get("rush") else ""
        due = job.get("due_date") or "—"
        st.markdown(
            f"<p class='detail-customer-name'>{job.get('_id', '—')} {rush_label}</p>"
            f"<p class='detail-customer-meta'>Parent: "
            f"{job.get('parent_id') or '— (standalone)'}</p>"
            f"<p class='detail-customer-meta'>Status: "
            f"<b>{job.get('status', '—')}</b>  ·  Phase: "
            f"{phase_pill(job.get('phase') or 'unknown')}</p>"
            f"<p class='detail-customer-meta'>Due: {due}</p>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr class='section-divider'/>", unsafe_allow_html=True)

    # ----- Declaration + action plan -------------------------------------
    _render_code_block("📜 shoptalk declaration", job.get("declaration_source") or "")
    _render_code_block("🧮 action plan (s-expression)", job.get("action_plan") or "")

    # ----- Out-of-scope notes --------------------------------------------
    notes = job.get("out_of_scope_notes") or []
    if notes:
        st.markdown("<div class='detail-block-label'>Out-of-scope notes</div>", unsafe_allow_html=True)
        for n in notes:
            st.markdown(f"- {n}")

    # ----- Flags ---------------------------------------------------------
    if flags:
        st.markdown("<div class='detail-block-label'>⚑ Flags</div>", unsafe_allow_html=True)
        for f in flags:
            st.markdown(
                "<div class='flag-card'>"
                f"<div class='flag-reason'>{f.get('reason', '—')}</div>"
                f"<div class='flag-context'>{f.get('context', '')}</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    # ----- Conversation --------------------------------------------------
    st.markdown("<div class='detail-block-label'>💬 Conversation</div>", unsafe_allow_html=True)
    messages = fetch_conversation(selected)
    if not messages:
        st.markdown(
            "<div class='empty-state'>No conversation turns yet.</div>",
            unsafe_allow_html=True,
        )
        return

    # Surface unsent draft replies at the top.
    drafts = [
        (i, m) for i, m in enumerate(messages)
        if m.get("role") == "agent_to_customer" and m.get("status") == "draft"
    ]
    for idx, draft in drafts:
        _render_draft_reply(selected, idx, draft)

    # Then render the full thread chronologically.
    for m in messages:
        ts = _to_dt(m.get("timestamp"))
        ts_label = ts.strftime("%H:%M:%S") if ts else "—"
        st.markdown(
            "<div class='turn'>"
            "<div class='turn-header'>"
            f"{role_badge(m.get('role', ''))}"
            f"{turn_status_badge(m.get('status', ''))}"
            f"<span class='turn-time'>{ts_label}</span>"
            "</div>"
            f"<div class='turn-content'>{_escape(m.get('content', ''))}</div>"
            "</div>",
            unsafe_allow_html=True,
        )


def _escape(s: str) -> str:
    """Minimal HTML escape — Streamlit's markdown surface lets raw HTML through."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_draft_reply(job_id: str, message_index: int, draft: dict[str, Any]) -> None:
    body = _escape(draft.get("content", ""))
    st.markdown(
        "<div class='draft-reply-card'>"
        "<div class='draft-reply-label'>💬 DRAFT REPLY AWAITING YOUR REVIEW</div>"
        f"<div class='draft-reply-body'>{body}</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button(
        "✉️ Send to Customer",
        key=f"send_{job_id}_{message_index}",
        type="primary",
    ):
        if mark_draft_sent(job_id, message_index):
            st.toast("Reply marked as sent.", icon="✅")
            time.sleep(0.2)
            st.rerun()
        else:
            st.error("Could not update the draft. Check MongoDB connectivity.")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_sidebar() -> bool:
    """Returns True if auto-refresh is enabled."""
    with st.sidebar:
        st.markdown("## ⚙️ Controls")
        auto_refresh = st.toggle("Auto-refresh (1s)", value=True)
        st.divider()
        st.markdown("### Phase legend")
        for phase in (
            "reading_message",
            "validating_spec",
            "ready_for_review",
            "clarification_needed",
            "human_review",
            "escalated",
            "done",
        ):
            st.markdown(phase_pill(phase), unsafe_allow_html=True)
        st.divider()
        st.caption(
            "Voomie agent runs spawn as background subprocesses.\n\n"
            "Logs: /tmp/voomie-runs/<run-id>/\n\n"
            "Uploads: /tmp/voomie-uploads/<run-id>/"
        )
        last = st.session_state.get("last_run_id")
        if last:
            st.caption(f"Last spawned run: `{last}`")
    return auto_refresh


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    auto_refresh = render_sidebar()
    if auto_refresh:
        st_autorefresh(interval=DEFAULT_REFRESH_MS, key="voomie_autorefresh")

    connection_ok, connection_err = check_connection()
    render_header(connection_ok, connection_err)

    if not connection_ok:
        st.markdown(
            "<div class='error-banner'>"
            "⚠️ Cannot connect to MongoDB. Auto-refresh will retry. "
            f"<br/><small>{_escape(connection_err or '')}</small>"
            "</div>",
            unsafe_allow_html=True,
        )

    render_paste_in_pane()

    jobs = fetch_all_jobs()
    render_queue_pane(jobs)

    st.markdown("<hr class='section-divider'/>", unsafe_allow_html=True)
    render_detail_pane(jobs)


# `streamlit run dashboard/app.py` executes the module as __main__, so this
# is the right entry point for both the CLI and direct invocation.
if __name__ == "__main__":
    main()
