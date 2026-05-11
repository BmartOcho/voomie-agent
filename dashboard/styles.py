"""
dashboard/styles.py — CSS for the Voomie CSR dashboard.

Status pill colors map to the SPEC.md/VALID_PHASES vocabulary:
  • Drafting phases (blue, pulsing): reading_message, checking_attachments,
    looking_up_customer, resolving_stocks, resolving_presses,
    checking_coatings, validating_dates, drafting_reply
  • validating_spec (purple, pulsing)
  • ready_for_review (green)
  • clarification_needed (yellow)
  • human_review (orange)
  • escalated (red)
  • done (gray)

Inject once at the top of app.py via st.markdown(STYLES, unsafe_allow_html=True).
"""

from __future__ import annotations

# Phases that should show the live "agent is actively working" pulse.
DRAFTING_PHASES = frozenset({
    "reading_message",
    "checking_attachments",
    "looking_up_customer",
    "resolving_stocks",
    "resolving_presses",
    "checking_coatings",
    "validating_dates",
    "drafting_reply",
})

VALIDATING_PHASES = frozenset({"validating_spec"})

# Buckets used by the queue filter dropdown.
PHASE_BUCKETS = {
    "In Progress": DRAFTING_PHASES | VALIDATING_PHASES,
    "Awaiting Review": frozenset({"ready_for_review", "clarification_needed"}),
    "Human Review": frozenset({"human_review", "escalated"}),
    "Done": frozenset({"done"}),
}


def phase_pill_class(phase: str) -> str:
    """Return the CSS class suffix for a given phase string."""
    if phase in DRAFTING_PHASES:
        return "phase-drafting"
    if phase in VALIDATING_PHASES:
        return "phase-validating"
    if phase == "ready_for_review":
        return "phase-ready"
    if phase == "clarification_needed":
        return "phase-clarification"
    if phase == "human_review":
        return "phase-human"
    if phase == "escalated":
        return "phase-escalated"
    if phase == "done":
        return "phase-done"
    return "phase-other"


def phase_pill(phase: str) -> str:
    """Return an HTML <span> for a phase status pill."""
    label = (phase or "unknown").replace("_", " ").upper()
    return (
        f"<span class='status-pill {phase_pill_class(phase or '')}'>{label}</span>"
    )


def customer_badge(kind: str) -> str:
    """Return an HTML <span> for a customer relationship badge."""
    kind = (kind or "").upper()
    cls = {
        "NEW": "badge-new",
        "RETURNING": "badge-returning",
        "WALK-IN": "badge-walkin",
    }.get(kind, "badge-other")
    return f"<span class='customer-badge {cls}'>{kind}</span>"


def role_badge(role: str) -> str:
    """Return an HTML <span> for a conversation-turn role badge."""
    label_map = {
        "user": ("USER", "role-user"),
        "agent": ("AGENT", "role-agent"),
        "agent_to_customer": ("AGENT TO CUSTOMER", "role-agent-customer"),
    }
    label, cls = label_map.get(role, (role.upper(), "role-other"))
    return f"<span class='role-badge {cls}'>{label}</span>"


def turn_status_badge(status: str) -> str:
    """Return an HTML <span> for a conversation-turn status badge (or empty)."""
    status = (status or "").lower()
    if status == "draft":
        return "<span class='turn-status status-draft'>DRAFT</span>"
    if status == "sent":
        return "<span class='turn-status status-sent'>SENT</span>"
    if status == "pending_review":
        return "<span class='turn-status status-pending'>PENDING REVIEW</span>"
    return ""


STYLES = """
<style>
:root {
  --voomie-fg:    #111827;
  --voomie-muted: #6B7280;
  --voomie-line:  #E5E7EB;
  --voomie-bg:    #F9FAFB;
}

.block-container { padding-top: 1.2rem; padding-bottom: 2.2rem; }

/* ---------- Header --------------------------------------------------- */
.voomie-wordmark {
  font-size: 2.0rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin: 0;
  color: var(--voomie-fg);
}
.voomie-subtitle {
  color: var(--voomie-muted);
  font-size: 0.95rem;
  margin: 0;
}
.connection-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  background: #F3F4F6;
  color: #374151;
  border: 1px solid var(--voomie-line);
}
.connection-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot-ok   { background: #16A34A; box-shadow: 0 0 0 4px rgba(22,163,74,0.15); }
.dot-fail { background: #DC2626; box-shadow: 0 0 0 4px rgba(220,38,38,0.15); }

/* ---------- Status pills (phase) ------------------------------------- */
.status-pill {
  display: inline-block;
  padding: 3px 12px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 0.72rem;
  letter-spacing: 0.04em;
  border: 1px solid transparent;
  white-space: nowrap;
}
.phase-drafting {
  background: #DBEAFE; color: #1E3A8A; border-color: #BFDBFE;
  animation: voomie-pulse 1.6s ease-in-out infinite;
}
.phase-validating {
  background: #EDE9FE; color: #5B21B6; border-color: #DDD6FE;
  animation: voomie-pulse 1.6s ease-in-out infinite;
}
.phase-ready        { background: #DCFCE7; color: #166534; border-color: #BBF7D0; }
.phase-clarification{ background: #FEF3C7; color: #854D0E; border-color: #FDE68A; }
.phase-human        { background: #FFEDD5; color: #9A3412; border-color: #FED7AA; }
.phase-escalated    { background: #FEE2E2; color: #991B1B; border-color: #FECACA; }
.phase-done         { background: #E5E7EB; color: #374151; border-color: #D1D5DB; }
.phase-other        { background: #F3F4F6; color: #374151; border-color: #E5E7EB; }

@keyframes voomie-pulse {
  0%,100% { opacity: 1.0; }
  50%     { opacity: 0.55; }
}

/* ---------- Customer badges ----------------------------------------- */
.customer-badge {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 4px;
  font-weight: 700;
  font-size: 0.66rem;
  letter-spacing: 0.06em;
  margin-left: 6px;
  vertical-align: middle;
}
.badge-new       { background: transparent; color: #1D4ED8; border: 1px solid #1D4ED8; }
.badge-returning { background: #B45309; color: #FFFBEB; border: 1px solid #92400E; }
.badge-walkin    { background: transparent; color: #6B7280; border: 1px solid #9CA3AF; }
.badge-other     { background: transparent; color: #6B7280; border: 1px solid #D1D5DB; }

/* ---------- Job group cards ----------------------------------------- */
.job-group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.job-group-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--voomie-fg);
}
.job-group-customer {
  color: var(--voomie-muted);
  font-size: 0.88rem;
  margin-left: 8px;
}
.job-group-time {
  color: var(--voomie-muted);
  font-size: 0.78rem;
  font-style: italic;
}
.child-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  border-top: 1px dashed var(--voomie-line);
}
.child-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85rem;
  color: var(--voomie-fg);
  font-weight: 600;
  min-width: 110px;
}
.child-summary {
  color: #374151;
  font-size: 0.9rem;
  flex: 1;
}
.flag-badge {
  display: inline-block;
  background: #FEE2E2;
  color: #991B1B;
  border: 1px solid #FECACA;
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 0.7rem;
  font-weight: 700;
}

/* ---------- Detail pane --------------------------------------------- */
.detail-block-label {
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--voomie-muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 2px;
}
.detail-customer-name {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--voomie-fg);
  margin: 0;
}
.detail-customer-meta {
  color: var(--voomie-muted);
  font-size: 0.85rem;
  margin: 2px 0;
}
.detail-notes {
  background: var(--voomie-bg);
  border-left: 3px solid #6366F1;
  padding: 6px 10px;
  font-size: 0.85rem;
  border-radius: 4px;
  margin-top: 4px;
}

/* ---------- Conversation turns -------------------------------------- */
.turn {
  border: 1px solid var(--voomie-line);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: white;
}
.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.turn-time {
  color: var(--voomie-muted);
  font-size: 0.72rem;
  margin-left: auto;
}
.turn-content {
  white-space: pre-wrap;
  font-size: 0.92rem;
  color: var(--voomie-fg);
  line-height: 1.4;
}
.role-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.role-user           { background: #DBEAFE; color: #1E40AF; }
.role-agent          { background: #E5E7EB; color: #374151; }
.role-agent-customer { background: #DCFCE7; color: #166534; }
.role-other          { background: #F3F4F6; color: #374151; }

.turn-status {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.status-draft   { background: #FEF3C7; color: #854D0E; }
.status-sent    { background: #E5E7EB; color: #374151; }
.status-pending { background: #FFEDD5; color: #9A3412; }

/* Draft replies awaiting CSR review get the eye-catching treatment. */
.draft-reply-card {
  border: 1px solid #F59E0B;
  border-left: 6px solid #F59E0B;
  background: #FFFBEB;
  padding: 12px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
}
.draft-reply-label {
  font-size: 0.78rem;
  font-weight: 700;
  color: #B45309;
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.draft-reply-body {
  white-space: pre-wrap;
  font-size: 0.95rem;
  color: var(--voomie-fg);
  line-height: 1.45;
  margin-bottom: 8px;
}

/* ---------- Code blocks (declaration / action plan) ----------------- */
.voomie-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: #0F172A;
  color: #E2E8F0;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 0.82rem;
  line-height: 1.4;
  overflow-x: auto;
  white-space: pre;
}
.voomie-code .kw   { color: #93C5FD; font-weight: 700; }
.voomie-code .lit  { color: #FCD34D; }
.voomie-code .str  { color: #86EFAC; }

/* ---------- Misc utility -------------------------------------------- */
.empty-state {
  text-align: center;
  color: var(--voomie-muted);
  padding: 28px 20px;
  border: 1px dashed var(--voomie-line);
  border-radius: 8px;
  background: var(--voomie-bg);
  font-size: 0.92rem;
}
.error-banner {
  background: #FEF2F2;
  border: 1px solid #FECACA;
  color: #991B1B;
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 0.9rem;
}
.section-divider {
  border: none;
  border-top: 1px solid var(--voomie-line);
  margin: 14px 0;
}
.flag-card {
  border: 1px solid #FECACA;
  background: #FEF2F2;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 6px;
  font-size: 0.88rem;
}
.flag-reason { font-weight: 700; color: #991B1B; }
.flag-context { color: #7F1D1D; margin-top: 2px; white-space: pre-wrap; }
</style>
"""
