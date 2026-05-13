"""
dashboard/styles.py — CSS for the Voomie CSR dashboard.

Dark-only color system grounded in CMYK process color theory. Every
color token has a documented CMYK origin — the CSR's day is spent
thinking in CMYK plates and our dashboard should reflect that. sRGB
values are tuned to pass WCAG AA on the dark ink background, not
literal CMYK→sRGB profile conversions (which would be muddier on
screen).

Token families:
  INK         — backgrounds. Rich black (C30 M30 Y30 K100), warm.
  PAPER       — foregrounds. Uncoated text-stock white, not pure #FFF.
  RULE        — dividers. Registration-thin.
  PROCESS M   — brand accent (the 100M plate). CTA, selection, working.
  READY       — 100C + 100Y plates → green. ready_for_review.
  AWAITING    — 100Y plate alone → yellow. clarification_needed.
  HUMAN       — 100M + 100Y plates → red. human_review / escalated.
  PROCESS C   — tertiary accent (the 100C plate). Info / NEW badge / links.
  DONE        — neutral gray. done / off-the-press.

Status pill colors map to the SPEC.md/VALID_PHASES vocabulary:
  • Drafting phases (magenta, pulsing dot + row shimmer): reading_message,
    checking_attachments, looking_up_customer, resolving_stocks,
    resolving_presses, checking_coatings, validating_dates, drafting_reply
  • validating_spec (magenta, same family as drafting)
  • ready_for_review (green — solid dot)
  • clarification_needed (yellow — open ring)
  • human_review / escalated (red — breathing dot)
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
    uses, so dot + tag always agree. Drafting/validating phases get
    the magenta pulse (via CSS animation), making the dot the only
    moving element on the page.
    """
    cls = phase_pill_class(phase or "").replace("phase-", "dot-")
    return f"<span class='state-dot {cls}'></span>"


# Maps raw phase -> the .tag-X CSS variant used for the inline status
# tag on each queue row. Five families correspond 1:1 to the five
# filter chips (working / ready / awaiting / human / done).
_PHASE_TAG_CLS = {
    "ready_for_review":      "ready",
    "clarification_needed":  "awaiting",
    "human_review":          "human",
    "escalated":             "human",
    "done":                  "done",
}


def state_tag(phase: str) -> str:
    """Return an HTML <span> for the row's inline status tag.

    Pairs with state_dot — the dot encodes status by color/shape, the
    tag encodes it as a readable word. Together they pass redundant
    encoding (color + text + shape) so colorblind users can still
    parse state.
    """
    raw = phase or "unknown"
    if raw in DRAFTING_PHASES or raw in VALIDATING_PHASES:
        cls, label = "working", "WORKING"
    elif raw in _PHASE_TAG_CLS:
        cls = _PHASE_TAG_CLS[raw]
        label = PHASE_DISPLAY_LABELS.get(raw, raw.replace("_", " ").upper())
    else:
        cls = "other"
        label = raw.replace("_", " ").upper()
    return f"<span class='tag tag-{cls}'>{label}</span>"


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


_TOKENS = """
:root {
  /* ============================================================
     TYPOGRAPHY — Geist + Geist Mono (see commit 2e85bac).
     ============================================================ */
  --font-display: 'Geist', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-body:    'Geist', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-mono:    'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace;

  --type-display:    700 26px/1.15  var(--font-display);    /* page title */
  --type-section:    600 20px/1.25  var(--font-display);    /* section heading */
  --type-subhead:    600 15px/1.30  var(--font-display);    /* card / block heading */
  --type-body:       400 14px/1.50  var(--font-body);       /* body */
  --type-body-sm:    400 13px/1.50  var(--font-body);       /* compact body */
  --type-caption:    500 11px/1.40  var(--font-body);       /* uppercase labels */
  --tracking-caps:   0.08em;

  /* Vertical rhythm — generous between zones, tight within. */
  --space-zone:      28px;   /* between major zones (snapshot ↔ intake ↔ queue) */
  --space-section:   16px;   /* between section heading and its content */
  --space-card:      12px;   /* between rows/items inside a card */
  --radius-card:     12px;   /* card corner radius */
  --radius-control:   8px;   /* button / input corner radius */

  /* ============================================================
     COLOR — CMYK-grounded. Every token below cites its plate mix.
     WCAG AA confirmed for every text consumer on ink-0 and ink-1.
     ============================================================ */

  /* INK — Background system.
     A pressroom "rich black" is C30 M30 Y30 K100, built from all four
     plates and warmer/deeper than pure K. Our ink-0 carries a faint
     M lean so it reads as press black, not monitor black. */
  --ink-0:        #0B0A0E;   /* page bg                  (rich black) */
  --ink-1:        #131218;   /* card / container surface (+1) */
  --ink-2:        #1B1A22;   /* raised surface / hover   (+2) */
  --ink-3:        #07060A;   /* deepest                  (K plate solid) */

  /* PAPER — Foreground system.
     Uncoated text stock is never pure #FFFFFF — it carries warmth
     from fiber. Pulling paper-0 toward cream lets the surface read
     as page-against-ink, not pixels-against-screen. */
  --paper-0:      #F2EDE3;   /* primary text (uncoated text-stock white) */
  --paper-1:      #B8B4AC;   /* secondary text */
  --paper-2:      #8A8680;   /* muted — passes AA at body sizes (5.45:1 on ink-0) */

  /* RULES — registration-thin dividers */
  --rule:         #232128;
  --rule-hi:      #322F3A;

  /* PROCESS M — Brand accent (the M plate: 100M 0C 0Y 0K).
     Used as hairline accents only — focus rings, selected indicators,
     working pulse, active filter chip, primary CTA. Never a large fill. */
  --process-m:        #D8208C;
  --process-m-fg:     #F472B6;   /* readable variant for text (7.45:1 on ink-0) */
  --process-m-soft:   rgba(216, 32, 140, 0.14);
  --process-m-edge:   rgba(216, 32, 140, 0.45);
  --process-m-glow:   rgba(216, 32, 140, 0.22);

  /* READY — 100C + 100Y plates → green.
     Cyan + yellow overprint produces process green. */
  --ready-fg:         #5BD389;
  --ready-soft:       rgba( 91, 211, 137, 0.12);
  --ready-edge:       rgba( 91, 211, 137, 0.32);

  /* AWAITING CUSTOMER — 100Y plate alone */
  --await-fg:         #F4C544;
  --await-soft:       rgba(244, 197,  68, 0.12);
  --await-edge:       rgba(244, 197,  68, 0.34);

  /* NEEDS HUMAN — 100M + 100Y plates → red */
  --human-fg:         #F26666;
  --human-soft:       rgba(242, 102, 102, 0.14);
  --human-edge:       rgba(242, 102, 102, 0.36);

  /* PROCESS C — Tertiary accent (the C plate: 100C 0M 0Y 0K).
     Reserved for informational moments: links, info chips, the NEW
     customer badge, "press cylinder" affordances. Keeping it separate
     from brand magenta means selection / working / CTA never compete
     with informational signals. */
  --process-c:        #4FB8C9;
  --process-c-soft:   rgba( 79, 184, 201, 0.12);
  --process-c-edge:   rgba( 79, 184, 201, 0.34);

  /* DONE / NEUTRAL — "off the press" */
  --done-fg:          #8A8680;
  --done-soft:        #1B1A22;
  --done-edge:        #2E2C36;

  /* Role badges (conversation turns) */
  --role-user-fg:     #93C5FD;
  --role-user-bg:     rgba( 59, 130, 246, 0.14);
  --role-agent-fg:    var(--paper-1);
  --role-agent-bg:    var(--ink-2);
  --role-out-fg:      var(--ready-fg);
  --role-out-bg:      var(--ready-soft);

  /* Connection pill */
  --pill-bg:          var(--ink-2);
  --pill-fg:          var(--paper-1);

  /* Card depth recipes — subtle inner highlight + hairline border + drop.
     The two-shadow stack lifts cards off the page background without
     a heavy stroke. Selected variant adds a magenta edge + outer glow
     so the visual link between a selected row and the detail panel is
     immediate. */
  --shadow-card:
    0 1px 0 0 rgba(255, 255, 255, 0.03) inset,
    0 0 0 1px var(--rule),
    0 8px 24px -16px rgba(0, 0, 0, 0.60);
  --shadow-card-selected:
    0 1px 0 0 rgba(255, 255, 255, 0.04) inset,
    0 0 0 1px var(--process-m-edge),
    0 0 0 4px var(--process-m-glow),
    0 12px 32px -16px rgba(216, 32, 140, 0.18);
}
"""


_STYLES_BODY = """

/* ====================================================================
   WEB FONTS — Geist + Geist Mono from Google Fonts.
   @import inside <style> blocks load before the rest of CSS applies,
   so the first paint already uses the brand type (no FOUT visible on
   subsequent autorefreshes since the browser caches the woff2 after
   the first fetch).
   ==================================================================== */
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Geist+Mono:wght@400;500;600&display=swap');

/* ====================================================================
   STREAMLIT CHROME — paint the app shell with our tokens.
   ==================================================================== */
[data-testid="stAppViewContainer"],
[data-testid="stApp"] {
  background: var(--ink-0);
  color: var(--paper-0);
  font-family: var(--font-body);
  font-feature-settings: "ss01" on, "cv11" on;
  -webkit-font-smoothing: antialiased;
}

/* Override Streamlit's stock Source-Sans/serif fallbacks across every
   widget surface so the typography is consistent everywhere — buttons,
   inputs, labels, markdown, toasts. */
body, button, input, textarea, select,
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] label,
.stTextInput label,
.stTextArea label,
.stFileUploader label,
.stToggle label,
.stCaption,
.stToast,
.stTooltipContent {
  font-family: var(--font-body) !important;
}

/* H3 sits in the page hierarchy as "section heading" — apply our type
   token instead of Streamlit's default. */
[data-testid="stMarkdownContainer"] h3 {
  font: var(--type-section);
  letter-spacing: -0.01em;
  color: var(--paper-0);
  margin-top: 24px;
  margin-bottom: 12px;
}

/* Hide Streamlit's default toolbar (Stop / Deploy / hamburger) and
   header chrome — judges should see only the dashboard surface, not
   the Streamlit-runner UI. The container stays in the DOM (some
   widgets rely on it for layout calculations) but is visually
   collapsed. */
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"] {
  display: none !important;
}
[data-testid="stHeader"] {
  background: transparent;
  height: 0;
  min-height: 0;
}
[data-testid="stSidebar"] {
  background: var(--ink-1);
  border-right: 1px solid var(--rule);
}
[data-testid="stSidebar"] * { color: var(--paper-0); }
[data-testid="stSidebar"] .stMarkdown small,
[data-testid="stSidebar"] .stCaption { color: var(--paper-2); }
[data-testid="stSidebar"] [data-testid="stCaption"] { color: var(--paper-2); }

/* Sidebar uppercase caption label — replaces the old "⚙ Controls" h2
   so the sidebar reads as background, not a competing surface. */
.sidebar-label {
  font: var(--type-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  color: var(--paper-2);
  margin: 4px 0 12px;
}

/* Spacer that pushes the Dev info expander to the bottom of the
   sidebar. Streamlit's sidebar is a flex column, so a flex-grow
   item between two real items expands to fill the gap. */
.sidebar-spacer {
  flex: 1 1 auto;
  min-height: 24px;
}

/* Dev info expander — calmer header so it reads as a footer. */
[data-testid="stSidebar"] [data-testid="stExpander"] details summary {
  font: var(--type-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  color: var(--paper-2);
}

.block-container {
  padding-top: 24px;
  padding-bottom: 80px;
  padding-left: 36px;
  padding-right: 36px;
  max-width: 1480px;
}

/* Streamlit's bordered container — cards. Inner-highlight + hairline
   border + faint drop. No heavy strokes; the depth comes from the
   stacked shadow recipe, not from a thick border. */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--ink-1);
  border: none !important;
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  padding: 18px 20px !important;
}

/* Form inputs (paste-in pane) */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
  background: var(--ink-2) !important;
  color: var(--paper-0) !important;
  border: 1px solid var(--rule) !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
  border-color: var(--process-m) !important;
  box-shadow: 0 0 0 3px var(--process-m-soft) !important;
}

/* Selectbox */
[data-testid="stSelectbox"] > div > div {
  background: var(--ink-2);
  border: 1px solid var(--rule);
  color: var(--paper-0);
}

/* Buttons — primary uses accent, secondary stays neutral */
.stButton button[kind="primary"] {
  background: var(--process-m);
  color: #FFFFFF;
  border: 1px solid var(--process-m);
  font-weight: 600;
}
.stButton button[kind="primary"]:hover {
  background: var(--process-m-fg);
  border-color: var(--process-m-fg);
}
.stButton button[kind="secondary"] {
  background: var(--ink-2);
  color: var(--paper-0);
  border: 1px solid var(--rule);
}
.stButton button[kind="secondary"]:hover {
  border-color: var(--process-m-edge);
  color: var(--process-m-fg);
}

/* File uploader */
[data-testid="stFileUploaderDropzone"] {
  background: var(--ink-2);
  border: 1px dashed var(--rule-hi);
  color: var(--paper-1);
}

/* ====================================================================
   HEADER
   ==================================================================== */
/* ----- Page header -------------------------------------------------- */
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: var(--space-zone);
  padding-bottom: 0;
}
.page-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-header-mark {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: linear-gradient(135deg, var(--process-m) 0%, #8B1057 100%);
  box-shadow: 0 0 16px 0 var(--process-m-glow);
  flex-shrink: 0;
}
/* Wordmark — uses !important so Streamlit's default h1/h2 rules
   (font-size: 2.25rem etc. set on .stApp h1) don't override the
   type-display token. */
h1.voomie-wordmark {
  font: var(--type-display) !important;
  letter-spacing: -0.02em !important;
  margin: 0 !important;
  padding: 0 !important;
  color: var(--paper-0) !important;
}
.voomie-subtitle {
  font: var(--type-body-sm);
  color: var(--paper-2);
  margin: 2px 0 0;
}
.connection-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  background: var(--pill-bg);
  color: var(--pill-fg);
  border: 1px solid var(--rule);
}
.connection-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot-ok   { background: var(--ready-fg);     box-shadow: 0 0 0 4px var(--ready-soft); }
.dot-fail { background: var(--human-fg); box-shadow: 0 0 0 4px var(--human-soft); }

/* Section label above the snapshot strip — uppercase caption rhythm. */
.queue-section-label {
  font: var(--type-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  color: var(--paper-2);
  margin: 8px 0 10px;
}

/* ====================================================================
   SNAPSHOT STRIP — stat tiles that double as filter chips.
   Each chip is an <a href="?filter=X"> so a click is a real URL change
   that Streamlit picks up via st.query_params; HTML markup means we
   can stack a large count over an uppercase label, which st.button
   can't do.
   ==================================================================== */
.snap-strip {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin-bottom: var(--space-zone);
}
.snap-chip {
  display: block;
  background: var(--ink-1);
  border-radius: var(--radius-card);
  padding: 16px 18px 14px;
  box-shadow: var(--shadow-card);
  cursor: pointer;
  text-decoration: none !important;
  color: var(--paper-0) !important;
  transition: transform 140ms ease, background-color 140ms ease;
}
.snap-chip:hover {
  transform: translateY(-1px);
  background: var(--ink-2);
}
.snap-chip.active {
  box-shadow: var(--shadow-card-selected);
  background: linear-gradient(180deg, var(--ink-1) 0%, rgba(216, 32, 140, 0.05) 100%);
}
.snap-count {
  font: var(--type-display);
  font-size: 28px;
  line-height: 1;
  letter-spacing: -0.02em;
  color: var(--paper-0);
  margin-bottom: 8px;
  font-variant-numeric: tabular-nums;
}
.snap-chip.active .snap-count { color: var(--process-m-fg); }
.snap-label {
  font: var(--type-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  color: var(--paper-2);
  /* Single-line by default at typical widths; if the chip is genuinely
     too narrow (e.g. 6+ chips on a sub-laptop viewport), let it wrap to
     2 lines rather than overflow the tile. */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.snap-chip.active .snap-label { color: var(--process-m-fg); }

/* ====================================================================
   INTAKE CARD
   ==================================================================== */
.intake-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.intake-title {
  font: var(--type-subhead);
  margin: 0;
  color: var(--paper-0);
  letter-spacing: -0.01em;
}
.intake-aux {
  font: var(--type-caption);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  color: var(--paper-2);
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
  background: var(--process-m-soft);
  color: var(--process-m-fg);
  border-color: var(--process-m-edge);
}
.phase-validating {
  background: var(--process-m-soft);
  color: var(--process-m-fg);
  border-color: var(--process-m-edge);
}
.phase-ready          { background: var(--ready-soft);      color: var(--ready-fg);      border-color: var(--ready-edge); }
.phase-clarification  { background: var(--await-soft);    color: var(--await-fg);    border-color: var(--await-edge); }
.phase-human          { background: var(--human-soft);  color: var(--human-fg);  border-color: var(--human-edge); }
.phase-escalated      { background: var(--human-soft);  color: var(--human-fg);  border-color: var(--human-edge); }
.phase-done           { background: var(--done-soft); color: var(--done-fg); border-color: var(--done-edge); }
.phase-other          { background: var(--done-soft); color: var(--done-fg); border-color: var(--done-edge); }

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
.badge-new       { color: var(--process-m-fg); border: 1px solid var(--process-m-edge); }
.badge-returning { color: var(--await-fg);   border: 1px solid var(--await-edge); }
.badge-walkin    { color: var(--paper-2);      border: 1px solid var(--rule-hi); }
.badge-other     { color: var(--paper-2);      border: 1px solid var(--rule); }

/* ====================================================================
   JOB GROUP CARDS
   ==================================================================== */
.job-group-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 10px;
  margin-bottom: 4px;
  border-bottom: 1px dashed var(--rule);
}
.job-group-jid {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--paper-0);
  letter-spacing: -0.01em;
}
.job-group-customer {
  color: var(--paper-1);
  font: var(--type-body-sm);
}
.job-group-time {
  margin-left: auto;
  color: var(--paper-2);
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

/* ====================================================================
   ROW — full-row clickable anchor.
   ==================================================================== */
.row {
  display: grid;
  grid-template-columns: 14px 110px 1fr auto auto auto;
  align-items: center;
  gap: 12px;
  padding: 9px 8px 9px 12px;
  border-radius: var(--radius-control);
  margin: 2px -4px;
  border-left: 3px solid transparent;
  text-decoration: none !important;
  color: var(--paper-0) !important;
  transition: background-color 120ms ease, border-left-color 120ms ease;
  position: relative;
}
.row:hover { background: var(--ink-2); }
.row-selected {
  background: var(--ink-2);
  border-left-color: var(--process-m);
}
/* Fresh: child updated within the last 30 seconds. The accent left
   border + faint magenta tint marks recent activity at a glance —
   without competing with the selected-row treatment which also uses
   the magenta border. */
.row-fresh {
  border-left-color: var(--process-m-edge);
  background: var(--process-m-soft);
}
.row-fresh:hover {
  background: var(--process-m-soft);
}

/* Working — agent is actively processing this job. A soft magenta
   gradient sweeps across the row bg on a 2.8s cycle. Combined with
   the pulsing state dot (1.4s), the row reads as "alive" without
   ever shouting.

   Stagger via --row-index (set inline on each working row by
   render_queue_list). animation-delay uses NEGATIVE multiplication
   so each row starts the cycle at a different point — three
   simultaneous working rows look like ambient activity, not
   three rows flickering in lockstep. */
.row-working {
  background-image: linear-gradient(
    90deg,
    transparent 0%,
    var(--process-m-glow) 50%,
    transparent 100%
  );
  background-size: 60% 100%;
  background-repeat: no-repeat;
  background-position: -150% 0;
  animation: voomie-row-shimmer 2.8s linear infinite;
  animation-delay: calc(var(--row-index, 0) * -0.45s);
}
/* Selected wins over working — keep the magenta left bar visible
   and don't compound a sweep on top of the ink-2 selected bg. */
.row-selected.row-working {
  background-image: none;
  animation: none;
}

@keyframes voomie-row-shimmer {
  0%   { background-position: -150% 0; }
  100% { background-position:  250% 0; }
}

/* Row internals. The grid template above defines six slots:
   [state-dot] [jid] [summary] [flag] [tag] [age]
   The summary fills the flex column; everything else hugs its content. */
.row-jid {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 500;
  color: var(--paper-1);
}
.row-selected .row-jid { color: var(--paper-0); }

.row-summary {
  color: var(--paper-0);
  font: var(--type-body-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* min-width: 0 is required to let an ellipsizable element shrink
     inside a CSS Grid track — grid items default to min-width:auto
     which respects intrinsic content width and prevents truncation
     when a sibling (the status tag) wants its full width. */
  min-width: 0;
}
.row-age {
  color: var(--paper-2);
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.row-fresh .row-age { color: var(--process-m-fg); font-weight: 600; }
.row-selected .row-age { color: var(--process-m-fg); }

.flag-badge {
  display: inline-block;
  background: var(--human-soft);
  color: var(--human-fg);
  border: 1px solid var(--human-edge);
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 600;
}

/* ====================================================================
   STATE DOT — distinct shapes per status (redundant encoding).
   - Ready    : solid filled green circle
   - Awaiting : open yellow ring (border, transparent fill)
   - Human    : solid red dot, breathing animation
   - Working  : solid magenta with glow + pulse animation
   - Done     : small dim gray dot
   ==================================================================== */
.state-dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  box-sizing: border-box;
}
.dot-ready {
  background: var(--ready-fg);
  box-shadow: 0 0 0 2px var(--ready-soft);
}
.dot-clarification {
  /* Open ring — fill stays transparent, the border IS the dot */
  background: transparent;
  border: 2px solid var(--await-fg);
  box-shadow: 0 0 0 2px var(--await-soft);
}
.dot-human,
.dot-escalated {
  background: var(--human-fg);
  box-shadow: 0 0 0 2px var(--human-soft);
  animation: voomie-breathe 2.4s ease-in-out infinite;
}
.dot-done,
.dot-other {
  width: 8px;
  height: 8px;
  background: var(--done-fg);
  opacity: 0.55;
}
.dot-drafting,
.dot-validating {
  background: var(--process-m);
  box-shadow: 0 0 0 2px var(--process-m-soft), 0 0 8px 0 var(--process-m-glow);
  animation: voomie-pulse 1.4s ease-in-out infinite;
}

@keyframes voomie-breathe {
  0%, 100% { opacity: 1.0; }
  50%      { opacity: 0.55; }
}

/* ====================================================================
   STATUS TAG — inline label that pairs with the state dot.
   ==================================================================== */
.tag {
  display: inline-block;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
}
.tag-ready    { color: var(--ready-fg);     background: var(--ready-soft);     border: 1px solid var(--ready-edge); }
.tag-awaiting { color: var(--await-fg);     background: var(--await-soft);     border: 1px solid var(--await-edge); }
.tag-human    { color: var(--human-fg);     background: var(--human-soft);     border: 1px solid var(--human-edge); }
.tag-working  { color: var(--process-m-fg); background: var(--process-m-soft); border: 1px solid var(--process-m-edge); }
.tag-done     { color: var(--done-fg);      background: var(--done-soft);      border: 1px solid var(--done-edge); }
.tag-other    { color: var(--paper-2);      background: var(--ink-2);          border: 1px solid var(--rule); }

/* ====================================================================
   DETAIL PANEL — split-pane right side.

   Selector strategy: target only the IMMEDIATE stVerticalBlock that
   contains the marker (the one from st.container()), not every
   ancestor stVerticalBlock up to the page root. The trick is to
   match via the explicit direct-child path
     stVerticalBlock > element-container > stMarkdown > stMarkdownContainer > .detail-panel-marker
   — every outer stVerticalBlock has a stVerticalBlockBorderWrapper
   or anonymous div between itself and an element-container, so the
   direct-child chain fails for those.

   The bare unscoped :has(.detail-panel-marker) version was applying
   the panel styling (magenta border, sticky positioning, padding) to
   every stVerticalBlock containing the marker — including the
   page-level block — which produced a magenta border around the
   whole main column and pushed content way down.
   ==================================================================== */
/* Target the column (right side of the split) directly when it
   contains the marker. Streamlit's column uses data-testid="column",
   not "stColumn" — different from the docs. */
[data-testid="column"]:has(.detail-panel-marker) {
  background: var(--ink-1);
  border-radius: var(--radius-card);
  border-left: 3px solid var(--process-m);
  padding: 22px 24px 24px !important;
  box-shadow: var(--shadow-card);
  position: sticky;
  top: 20px;
}
.detail-panel-marker {
  /* Marker only — invisible, takes no layout space. */
  display: none;
}
.detail-close {
  margin-left: 6px;
  color: var(--paper-2);
  text-decoration: none !important;
  font-size: 22px;
  line-height: 1;
  padding: 0 6px;
  border-radius: 6px;
  transition: color 120ms ease, background-color 120ms ease;
}
.detail-close:hover {
  color: var(--paper-0);
  background: var(--ink-2);
}

/* ====================================================================
   DETAIL — inner blocks
   ==================================================================== */
.detail-block-label {
  font: var(--type-caption);
  font-weight: 700;
  color: var(--paper-2);
  letter-spacing: var(--tracking-caps);
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
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 600;
  color: var(--paper-0);
  letter-spacing: -0.01em;
}
.detail-rush-flag {
  background: var(--human-soft);
  color: var(--human-fg);
  border: 1px solid var(--human-edge);
  padding: 2px 8px;
  border-radius: 4px;
  font: var(--type-caption);
  font-weight: 700;
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
}
.detail-header-age {
  color: var(--paper-2);
  font-family: var(--font-mono);
  font-size: 11px;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.detail-customer-name-inline {
  font: var(--type-subhead);
  color: var(--paper-0);
}
.detail-customer-meta-inline {
  color: var(--paper-2);
  font: var(--type-body-sm);
}

/* Legacy classes — preserved in case anything still renders them.
   New code paths should prefer the *-inline variants above. */
.detail-customer-name {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--paper-0);
  margin: 0;
}
.detail-customer-meta {
  color: var(--paper-1);
  font-size: 0.85rem;
  margin: 2px 0;
}

.detail-notes {
  background: var(--ink-2);
  border-left: 3px solid var(--process-m);
  padding: 6px 10px;
  font-size: 0.85rem;
  border-radius: 4px;
  margin: 4px 0 14px;
  color: var(--paper-0);
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
  color: var(--paper-2);
  font: var(--type-caption);
  font-weight: 600;
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  align-self: center;
}
.spec-val {
  color: var(--paper-0);
  font-family: var(--font-mono);
  font-size: 13px;
}
.empty-state-inline {
  color: var(--paper-2);
  font-size: 0.85rem;
  font-style: italic;
  padding: 4px 0;
}

/* ====================================================================
   CONVERSATION TURNS
   ==================================================================== */
.turn {
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: var(--ink-1);
}
.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.turn-time {
  color: var(--paper-2);
  font-size: 0.72rem;
  margin-left: auto;
}
.turn-content {
  white-space: pre-wrap;
  font-size: 0.92rem;
  color: var(--paper-0);
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
.role-other          { background: var(--done-soft);  color: var(--done-fg); }

.turn-status {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.status-draft   { background: var(--await-soft);    color: var(--await-fg);    border: 1px solid var(--await-edge); }
.status-sent    { background: var(--done-soft); color: var(--done-fg); border: 1px solid var(--done-edge); }
.status-pending { background: var(--await-soft);    color: var(--await-fg);    border: 1px solid var(--await-edge); }

/* Draft replies awaiting CSR review get the eye-catching treatment. */
.draft-reply-card {
  border: 1px solid var(--await-edge);
  border-left: 4px solid var(--await-fg);
  background: var(--await-soft);
  padding: 12px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
}
.draft-reply-label {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--await-fg);
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.draft-reply-body {
  white-space: pre-wrap;
  font-size: 0.95rem;
  color: var(--paper-0);
  line-height: 1.45;
  margin-bottom: 8px;
}

/* ====================================================================
   CODE BLOCKS (declaration / action plan)
   Code stays dark in both themes — easier to read shoptalk source.
   ==================================================================== */
.voomie-code {
  font-family: var(--font-mono);
  background: var(--ink-3);
  color: #E2E8F0;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 0.82rem;
  line-height: 1.4;
  overflow-x: auto;
  white-space: pre;
  border: 1px solid var(--rule);
}
.voomie-code .kw   { color: #93C5FD; font-weight: 700; }
.voomie-code .lit  { color: #FCD34D; }
.voomie-code .str  { color: #86EFAC; }

/* Streamlit's built-in code block (st.code) — match. */
[data-testid="stCodeBlock"] pre,
.stCodeBlock pre {
  background: var(--ink-3) !important;
  color: #E2E8F0 !important;
  border: 1px solid var(--rule) !important;
}

/* Expanders */
.streamlit-expanderHeader,
[data-testid="stExpander"] summary {
  background: var(--ink-2);
  color: var(--paper-0);
  border: 1px solid var(--rule);
  border-radius: 6px;
}

/* ====================================================================
   MISC UTILITY
   ==================================================================== */
.empty-state {
  text-align: center;
  color: var(--paper-1);
  padding: 28px 20px;
  border: 1px dashed var(--rule);
  border-radius: 8px;
  background: var(--ink-2);
  font-size: 0.92rem;
}
.error-banner {
  background: var(--human-soft);
  border: 1px solid var(--human-edge);
  color: var(--human-fg);
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 0.9rem;
}
.section-divider {
  border: none;
  border-top: 1px solid var(--rule);
  margin: 14px 0;
}
.flag-card {
  border: 1px solid var(--human-edge);
  background: var(--human-soft);
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 6px;
  font-size: 0.88rem;
}
.flag-reason { font-weight: 700; color: var(--human-fg); }
.flag-context { color: var(--paper-1); margin-top: 2px; white-space: pre-wrap; }
"""


STYLES = f"<style>{_TOKENS}{_STYLES_BODY}</style>"
