"""
dashboard/styles.py — CSS for the Voomie CSR dashboard.

Theme system: CSS custom properties on `:root` (dark default) with a
`:root[data-theme="light"]` override block for the future toggle. Every
color in the surface goes through `var(--...)` so the toggle in
dashboard/app.py can flip the whole UI by setting one data-attribute.

Brand accent is Process Magenta (#D8208C, the "M" plate in CMYK) — used
as hairline accents only (focus rings, selected state, working dot,
active filter chip), never as a large fill.

Status pill colors map to the SPEC.md/VALID_PHASES vocabulary:
  • Drafting phases (magenta, pulsing): reading_message, checking_attachments,
    looking_up_customer, resolving_stocks, resolving_presses,
    checking_coatings, validating_dates, drafting_reply
  • validating_spec (magenta, pulsing — same family as drafting)
  • ready_for_review (green)
  • clarification_needed (amber)
  • human_review / escalated (red)
  • done (neutral gray)

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

# Buckets used by the queue filter chips.
#
# The tri-state split: ready_for_review, clarification_needed, and
# human_review/escalated each get their own bucket. Previously
# ready_for_review and clarification_needed were merged under one
# "Awaiting Review" label, but clarification_needed fires in ~70% of
# fixture runs and represents a distinct CSR action ("send the draft
# clarifying question to the customer") vs ready_for_review ("send the
# finished spec"). Treating them as one bucket hid that asymmetry.
PHASE_BUCKETS = {
    "Working":           DRAFTING_PHASES | VALIDATING_PHASES,
    "Ready":             frozenset({"ready_for_review"}),
    "Awaiting customer": frozenset({"clarification_needed"}),
    "Needs human":       frozenset({"human_review", "escalated"}),
    "Done":              frozenset({"done"}),
}

# Canonical filter-chip order; same order is used in the snapshot
# strip so counts and chips line up.
PHASE_BUCKET_ORDER = (
    "Working",
    "Ready",
    "Awaiting customer",
    "Needs human",
    "Done",
)

# Pill labels — short, action-oriented strings displayed in the queue.
# Maps the raw phase value (as written by the agent) to the CSR-facing
# verb. Anything not in this map falls back to the .upper() of the raw
# phase value with underscores replaced.
PHASE_DISPLAY_LABELS = {
    "ready_for_review":      "READY",
    "clarification_needed":  "AWAITING CUSTOMER",
    "human_review":          "NEEDS HUMAN",
    "escalated":             "ESCALATED",
    "done":                  "DONE",
    "validating_spec":       "VALIDATING",
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
    """Return an HTML <span> for a phase status pill.

    Uses PHASE_DISPLAY_LABELS to surface short, action-oriented copy
    where defined; falls back to the underscore-stripped uppercase of
    the raw phase value otherwise. Drafting phases (8 of them) all share
    one "WORKING" label since the per-phase distinction matters in the
    sidebar legend but is noisy in the queue.
    """
    raw = phase or "unknown"
    if raw in PHASE_DISPLAY_LABELS:
        label = PHASE_DISPLAY_LABELS[raw]
    elif raw in DRAFTING_PHASES:
        label = "WORKING"
    else:
        label = raw.replace("_", " ").upper()
    return (
        f"<span class='status-pill {phase_pill_class(raw)}'>{label}</span>"
    )


def state_dot(phase: str) -> str:
    """Return an HTML <span> for the row's leading state dot.

    Reuses phase_pill_class to derive the same color family the pill
    uses, so dot + pill always agree. Drafting/validating phases get
    the magenta pulse (via CSS animation), making the dot the only
    moving element on the page.
    """
    cls = phase_pill_class(phase or "").replace("phase-", "dot-")
    return f"<span class='state-dot {cls}'></span>"


def phase_legend_pill(phase: str) -> str:
    """Sidebar-legend pill that always shows the full phase name.

    Used only by the sidebar's phase legend, where the operator wants
    to see every distinct phase value the agent might emit — not the
    queue-friendly "WORKING" rollup.
    """
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
/* ====================================================================
   THEME TOKENS
   Dark is the default; light is opt-in via [data-theme="light"] on the
   document root. All surface colors flow through these vars — no
   component should hard-code a hex value.
   ==================================================================== */
:root {
  /* Surface — dark default */
  --bg-0:        #0A0A0A;   /* page bg */
  --bg-1:        #111111;   /* card / container bg */
  --bg-2:        #1A1A1A;   /* raised surface, row hover */
  --bg-3:        #050505;   /* code blocks (deepest) */

  /* Foreground */
  --fg-0:        #FAFAFA;   /* primary text */
  --fg-1:        #A1A1AA;   /* secondary text */
  --fg-2:        #71717A;   /* muted text */

  /* Borders / dividers */
  --border:        #262626;
  --border-strong: #333333;

  /* Brand accent — Process Magenta, the "M" in CMYK.
     Used as hairline accents only (focus rings, selected indicators,
     working-state dot, active filter chips). Never as large fills. */
  --accent:        #D8208C;
  --accent-fg:     #F472B6;   /* readable magenta on dark bg */
  --accent-soft:   rgba(216, 32, 140, 0.12);
  --accent-border: rgba(216, 32, 140, 0.35);

  /* Status — green / amber / red / neutral.
     Soft bg + bright fg + faint border = Linear-style calm pills. */
  --ok-fg:         #4ADE80;
  --ok-soft:       rgba( 34, 197,  94, 0.12);
  --ok-border:     rgba( 74, 222, 128, 0.30);

  --warn-fg:       #FBBF24;
  --warn-soft:     rgba(245, 158,  11, 0.12);
  --warn-border:   rgba(251, 191,  36, 0.30);

  --danger-fg:     #F87171;
  --danger-soft:   rgba(239,  68,  68, 0.15);
  --danger-border: rgba(248, 113, 113, 0.35);

  --neutral-fg:     #A1A1AA;
  --neutral-soft:   #1F1F1F;
  --neutral-border: #2A2A2A;

  /* Role badges (conversation turns) */
  --role-user-fg:    #93C5FD;
  --role-user-bg:    rgba( 59, 130, 246, 0.14);
  --role-agent-fg:   #A1A1AA;
  --role-agent-bg:   #1F1F1F;
  --role-out-fg:     #4ADE80;
  --role-out-bg:     rgba( 34, 197,  94, 0.12);

  /* Connection pill */
  --pill-bg:     #1A1A1A;
  --pill-fg:     #A1A1AA;
}

/* Light override — applied when the document root has data-theme="light".
   The toggle (sidebar) flips this attribute via session state. */
:root[data-theme="light"] {
  --bg-0:        #FAFAFA;
  --bg-1:        #FFFFFF;
  --bg-2:        #F4F4F5;
  --bg-3:        #0F172A;   /* code stays dark in both themes by design */

  --fg-0:        #0A0A0A;
  --fg-1:        #52525B;
  --fg-2:        #71717A;

  --border:        #E4E4E7;
  --border-strong: #D4D4D8;

  --accent:        #C2185B;
  --accent-fg:     #C2185B;
  --accent-soft:   rgba(216, 32, 140, 0.10);
  --accent-border: rgba(216, 32, 140, 0.40);

  --ok-fg:         #166534;
  --ok-soft:       rgba( 22, 163,  94, 0.10);
  --ok-border:     #BBF7D0;

  --warn-fg:       #92400E;
  --warn-soft:     rgba(245, 158,  11, 0.10);
  --warn-border:   #FDE68A;

  --danger-fg:     #991B1B;
  --danger-soft:   rgba(220,  38,  38, 0.08);
  --danger-border: #FECACA;

  --neutral-fg:     #52525B;
  --neutral-soft:   #F4F4F5;
  --neutral-border: #E4E4E7;

  --role-user-fg:   #1E40AF;
  --role-user-bg:   #DBEAFE;
  --role-agent-fg:  #374151;
  --role-agent-bg:  #E5E7EB;
  --role-out-fg:    #166534;
  --role-out-bg:    #DCFCE7;

  --pill-bg:     #F3F4F6;
  --pill-fg:     #374151;
}

/* ====================================================================
   STREAMLIT CHROME — paint the app shell with our tokens.
   ==================================================================== */
[data-testid="stAppViewContainer"],
[data-testid="stApp"] {
  background: var(--bg-0);
  color: var(--fg-0);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] {
  background: var(--bg-1);
  border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] * { color: var(--fg-0); }
[data-testid="stSidebar"] .stMarkdown small,
[data-testid="stSidebar"] .stCaption { color: var(--fg-2); }

.block-container { padding-top: 1.2rem; padding-bottom: 2.2rem; }

/* Streamlit's bordered container — use as cards. */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--bg-1);
  border: 1px solid var(--border) !important;
  border-radius: 10px;
}

/* Form inputs (paste-in pane) */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
  background: var(--bg-2) !important;
  color: var(--fg-0) !important;
  border: 1px solid var(--border) !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px var(--accent-soft) !important;
}

/* Selectbox */
[data-testid="stSelectbox"] > div > div {
  background: var(--bg-2);
  border: 1px solid var(--border);
  color: var(--fg-0);
}

/* Buttons — primary uses accent, secondary stays neutral */
.stButton button[kind="primary"] {
  background: var(--accent);
  color: #FFFFFF;
  border: 1px solid var(--accent);
  font-weight: 600;
}
.stButton button[kind="primary"]:hover {
  background: var(--accent-fg);
  border-color: var(--accent-fg);
}
.stButton button[kind="secondary"] {
  background: var(--bg-2);
  color: var(--fg-0);
  border: 1px solid var(--border);
}
.stButton button[kind="secondary"]:hover {
  border-color: var(--accent-border);
  color: var(--accent-fg);
}

/* File uploader */
[data-testid="stFileUploaderDropzone"] {
  background: var(--bg-2);
  border: 1px dashed var(--border-strong);
  color: var(--fg-1);
}

/* ====================================================================
   HEADER
   ==================================================================== */
.voomie-wordmark {
  font-size: 2.0rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin: 0;
  color: var(--fg-0);
}
.voomie-subtitle {
  color: var(--fg-1);
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
  background: var(--pill-bg);
  color: var(--pill-fg);
  border: 1px solid var(--border);
}
.connection-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot-ok   { background: var(--ok-fg);     box-shadow: 0 0 0 4px var(--ok-soft); }
.dot-fail { background: var(--danger-fg); box-shadow: 0 0 0 4px var(--danger-soft); }

/* ====================================================================
   FILTER CHIP STRIP (snapshot + filter)
   Targets the row of buttons immediately following the anchor div in
   render_filter_chips(). Tighter padding, smaller font, monospaced
   count separator — reads as a snapshot strip, not a row of buttons.
   ==================================================================== */
.filter-chip-strip-anchor + [data-testid="stHorizontalBlock"] {
  gap: 8px !important;
  margin-bottom: 8px;
}
.filter-chip-strip-anchor + [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
  padding: 0 !important;
}
.filter-chip-strip-anchor + [data-testid="stHorizontalBlock"] .stButton button {
  padding: 8px 10px !important;
  font-size: 0.78rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.02em;
  border-radius: 8px !important;
  min-height: 0 !important;
  line-height: 1.2 !important;
  text-align: left !important;
  justify-content: flex-start !important;
}
.filter-chip-strip-anchor + [data-testid="stHorizontalBlock"] .stButton button[kind="secondary"] {
  background: var(--bg-1);
}
.filter-chip-strip-anchor + [data-testid="stHorizontalBlock"] .stButton button[kind="secondary"]:hover {
  background: var(--bg-2);
}

/* ====================================================================
   STATUS PILLS (phase)
   Linear-style: soft tinted bg + bright fg + faint border. The
   "drafting" / "validating" pills pulse to signal live agent work.
   ==================================================================== */
.status-pill {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 0.70rem;
  letter-spacing: 0.05em;
  border: 1px solid transparent;
  white-space: nowrap;
}
.phase-drafting {
  background: var(--accent-soft);
  color: var(--accent-fg);
  border-color: var(--accent-border);
}
.phase-validating {
  background: var(--accent-soft);
  color: var(--accent-fg);
  border-color: var(--accent-border);
}
.phase-ready          { background: var(--ok-soft);      color: var(--ok-fg);      border-color: var(--ok-border); }
.phase-clarification  { background: var(--warn-soft);    color: var(--warn-fg);    border-color: var(--warn-border); }
.phase-human          { background: var(--danger-soft);  color: var(--danger-fg);  border-color: var(--danger-border); }
.phase-escalated      { background: var(--danger-soft);  color: var(--danger-fg);  border-color: var(--danger-border); }
.phase-done           { background: var(--neutral-soft); color: var(--neutral-fg); border-color: var(--neutral-border); }
.phase-other          { background: var(--neutral-soft); color: var(--neutral-fg); border-color: var(--neutral-border); }

@keyframes voomie-pulse {
  0%,100% { opacity: 1.0; }
  50%     { opacity: 0.55; }
}

/* ====================================================================
   CUSTOMER BADGES
   ==================================================================== */
.customer-badge {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 4px;
  font-weight: 700;
  font-size: 0.66rem;
  letter-spacing: 0.06em;
  margin-left: 6px;
  vertical-align: middle;
  background: transparent;
}
.badge-new       { color: var(--accent-fg); border: 1px solid var(--accent-border); }
.badge-returning { color: var(--warn-fg);   border: 1px solid var(--warn-border); }
.badge-walkin    { color: var(--fg-2);      border: 1px solid var(--border-strong); }
.badge-other     { color: var(--fg-2);      border: 1px solid var(--border); }

/* ====================================================================
   JOB GROUP CARDS / CHILD ROWS
   ==================================================================== */
.job-group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.job-group-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--fg-0);
}
.job-group-customer {
  color: var(--fg-1);
  font-size: 0.88rem;
  margin-left: 8px;
}
.job-group-time {
  color: var(--fg-2);
  font-size: 0.78rem;
  font-style: italic;
}
.child-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-top: 1px dashed var(--border);
  border-left: 3px solid transparent;
  margin-left: -10px;     /* compensate for left-border width so content stays aligned */
  padding-left: 7px;      /* with the group header above */
  transition: background-color 120ms ease, border-left-color 120ms ease;
}
.child-row:hover {
  background: var(--bg-2);
}
/* Fresh: child updated within the last 30 seconds. The left accent border
   + bg lift makes recent activity scannable at a glance — exactly what
   the queue is for. */
.child-row-fresh {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}
.child-row-fresh:hover {
  background: var(--accent-soft);
}

/* State dot — small colored circle to the left of every row, redundant
   visual encoding with the pill text. Drafting/validating phases get a
   pulse here (instead of on the pill) so motion is contained to a single
   small element rather than fading whole rows in/out. */
.state-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 0 3px transparent;
}
.dot-ready         { background: var(--ok-fg); }
.dot-clarification { background: var(--warn-fg); }
.dot-human         { background: var(--danger-fg); }
.dot-escalated     { background: var(--danger-fg); }
.dot-done          { background: var(--neutral-fg); }
.dot-other         { background: var(--neutral-fg); }
.dot-drafting,
.dot-validating {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  animation: voomie-pulse 1.6s ease-in-out infinite;
}

.child-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85rem;
  color: var(--fg-0);
  font-weight: 600;
  min-width: 110px;
}
.child-summary {
  color: var(--fg-1);
  font-size: 0.9rem;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.child-age {
  color: var(--fg-2);
  font-size: 0.72rem;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.child-age-fresh {
  color: var(--accent-fg);
  font-weight: 600;
}
.flag-badge {
  display: inline-block;
  background: var(--danger-soft);
  color: var(--danger-fg);
  border: 1px solid var(--danger-border);
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 0.7rem;
  font-weight: 700;
}

/* ====================================================================
   DETAIL PANE
   ==================================================================== */
.detail-block-label {
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--fg-2);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 4px;
}

/* Compact two-line header — replaces the previous two-column
   customer/job block. The intent: jid + state + age on line 1,
   customer identity on line 2. Anything more belongs in the grid. */
.detail-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}
.detail-header-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.detail-jid {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--fg-0);
  letter-spacing: -0.01em;
}
.detail-rush-flag {
  background: var(--danger-soft);
  color: var(--danger-fg);
  border: 1px solid var(--danger-border);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.detail-header-age {
  color: var(--fg-2);
  font-size: 0.78rem;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.detail-customer-name-inline {
  font-size: 0.98rem;
  font-weight: 600;
  color: var(--fg-0);
}
.detail-customer-meta-inline {
  color: var(--fg-1);
  font-size: 0.85rem;
}

/* Legacy classes — preserved in case anything still renders them.
   New code paths should prefer the *-inline variants above. */
.detail-customer-name {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--fg-0);
  margin: 0;
}
.detail-customer-meta {
  color: var(--fg-1);
  font-size: 0.85rem;
  margin: 2px 0;
}

.detail-notes {
  background: var(--bg-2);
  border-left: 3px solid var(--accent);
  padding: 6px 10px;
  font-size: 0.85rem;
  border-radius: 4px;
  margin: 4px 0 14px;
  color: var(--fg-0);
}

/* Two-column key:value grid used for both "Specs" (parsed from the
   shoptalk declaration) and "Job" meta (parent / due / rush / status).
   max-content for the label column keeps long values from pushing the
   labels around. */
.spec-grid {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 14px;
  font-size: 0.88rem;
  padding: 6px 0 10px;
}
.spec-key {
  color: var(--fg-2);
  font-size: 0.74rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  align-self: center;
}
.spec-val {
  color: var(--fg-0);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.86rem;
}
.empty-state-inline {
  color: var(--fg-2);
  font-size: 0.85rem;
  font-style: italic;
  padding: 4px 0;
}

/* ====================================================================
   CONVERSATION TURNS
   ==================================================================== */
.turn {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: var(--bg-1);
}
.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.turn-time {
  color: var(--fg-2);
  font-size: 0.72rem;
  margin-left: auto;
}
.turn-content {
  white-space: pre-wrap;
  font-size: 0.92rem;
  color: var(--fg-0);
  line-height: 1.45;
}
.role-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.role-user           { background: var(--role-user-bg);  color: var(--role-user-fg); }
.role-agent          { background: var(--role-agent-bg); color: var(--role-agent-fg); }
.role-agent-customer { background: var(--role-out-bg);   color: var(--role-out-fg); }
.role-other          { background: var(--neutral-soft);  color: var(--neutral-fg); }

.turn-status {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.status-draft   { background: var(--warn-soft);    color: var(--warn-fg);    border: 1px solid var(--warn-border); }
.status-sent    { background: var(--neutral-soft); color: var(--neutral-fg); border: 1px solid var(--neutral-border); }
.status-pending { background: var(--warn-soft);    color: var(--warn-fg);    border: 1px solid var(--warn-border); }

/* Draft replies awaiting CSR review get the eye-catching treatment. */
.draft-reply-card {
  border: 1px solid var(--warn-border);
  border-left: 4px solid var(--warn-fg);
  background: var(--warn-soft);
  padding: 12px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
}
.draft-reply-label {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--warn-fg);
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.draft-reply-body {
  white-space: pre-wrap;
  font-size: 0.95rem;
  color: var(--fg-0);
  line-height: 1.45;
  margin-bottom: 8px;
}

/* ====================================================================
   CODE BLOCKS (declaration / action plan)
   Code stays dark in both themes — easier to read shoptalk source.
   ==================================================================== */
.voomie-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--bg-3);
  color: #E2E8F0;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 0.82rem;
  line-height: 1.4;
  overflow-x: auto;
  white-space: pre;
  border: 1px solid var(--border);
}
.voomie-code .kw   { color: #93C5FD; font-weight: 700; }
.voomie-code .lit  { color: #FCD34D; }
.voomie-code .str  { color: #86EFAC; }

/* Streamlit's built-in code block (st.code) — match. */
[data-testid="stCodeBlock"] pre,
.stCodeBlock pre {
  background: var(--bg-3) !important;
  color: #E2E8F0 !important;
  border: 1px solid var(--border) !important;
}

/* Expanders */
.streamlit-expanderHeader,
[data-testid="stExpander"] summary {
  background: var(--bg-2);
  color: var(--fg-0);
  border: 1px solid var(--border);
  border-radius: 6px;
}

/* ====================================================================
   MISC UTILITY
   ==================================================================== */
.empty-state {
  text-align: center;
  color: var(--fg-1);
  padding: 28px 20px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  background: var(--bg-2);
  font-size: 0.92rem;
}
.error-banner {
  background: var(--danger-soft);
  border: 1px solid var(--danger-border);
  color: var(--danger-fg);
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 0.9rem;
}
.section-divider {
  border: none;
  border-top: 1px solid var(--border);
  margin: 14px 0;
}
.flag-card {
  border: 1px solid var(--danger-border);
  background: var(--danger-soft);
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 6px;
  font-size: 0.88rem;
}
.flag-reason { font-weight: 700; color: var(--danger-fg); }
.flag-context { color: var(--fg-1); margin-top: 2px; white-space: pre-wrap; }
</style>
"""
