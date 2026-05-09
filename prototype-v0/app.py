"""
PressFlow AI — Prepress Dashboard
Streamlit UI that watches MongoDB while run_agent.py works the queue.

Run with:  streamlit run app.py
"""

import time
from datetime import datetime

import pymongo
import streamlit as st

CONNECTION_STRING = "YOUR_MONGODB_CONNECTION_STRING_HERE"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PressFlow AI — Prepress Dashboard",
    page_icon="🖨️",
    layout="wide",
)

CUSTOM_CSS = """
<style>
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
.status-pill {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 0.85rem;
    letter-spacing: 0.02em;
}
.status-new        { background: #DBEAFE; color: #1E40AF; }
.status-ready      { background: #DCFCE7; color: #166534; }
.status-hold       { background: #FEE2E2; color: #991B1B; }
.status-other      { background: #E5E7EB; color: #374151; }
.job-id { font-size: 1.4rem; font-weight: 700; margin-bottom: 0; }
.customer { color: #6B7280; margin-top: 0; }
.notes-box {
    background: #F9FAFB;
    border-left: 4px solid #6366F1;
    padding: 10px 14px;
    border-radius: 6px;
    margin-top: 8px;
    font-size: 0.92rem;
}
.notes-empty {
    color: #9CA3AF;
    font-style: italic;
    font-size: 0.85rem;
    margin-top: 8px;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Mongo helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_mongo_client():
    return pymongo.MongoClient(CONNECTION_STRING, serverSelectionTimeoutMS=5000)


def fetch_jobs():
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        collection = client["print_shop"]["active_jobs"]
        jobs = list(collection.find({}, {"_id": 0}))
        return jobs, None
    except Exception as e:
        return [], str(e)


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Dashboard Controls")
    auto_refresh = st.toggle("Auto-refresh", value=True)
    interval = st.slider("Refresh interval (sec)", 1, 15, 3)

    st.divider()
    st.markdown("### Status Legend")
    st.markdown(
        "<span class='status-pill status-new'>NEW</span> awaiting agent",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<span class='status-pill status-ready'>PREPRESS READY</span> passed checks",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<span class='status-pill status-hold'>HOLD</span> human-in-the-loop",
        unsafe_allow_html=True,
    )

    st.divider()
    st.caption(
        "Run the agent in another terminal:\n\n"
        "`python run_agent.py`\n\n"
        "Statuses will flow in real-time."
    )

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
header_l, header_r = st.columns([4, 1])
with header_l:
    st.title("🖨️ PressFlow AI")
    st.caption(
        "Prepress Coordinator Dashboard — agent decisions surfaced for "
        "**human-in-the-loop** review."
    )
with header_r:
    st.markdown(f"**Last refresh**\n\n`{datetime.now().strftime('%H:%M:%S')}`")
    if st.button("🔄 Refresh now", use_container_width=True):
        st.rerun()

# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------
jobs, err = fetch_jobs()
if err:
    st.error(f"❌ MongoDB connection failed: {err}")
    st.info("Check your connection string or network and click **Refresh now**.")
    st.stop()

# ---------------------------------------------------------------------------
# Top-line metrics
# ---------------------------------------------------------------------------
def is_hold(s: str) -> bool:
    return isinstance(s, str) and s.lower().startswith("hold")


total = len(jobs)
new_count = sum(1 for j in jobs if j.get("status") == "new")
ready_count = sum(1 for j in jobs if j.get("status") == "prepress_ready")
hold_count = sum(1 for j in jobs if is_hold(j.get("status", "")))
processed = ready_count + hold_count
pct = int((processed / total) * 100) if total else 0

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("📦 Total Jobs", total)
m2.metric("🟦 New / Pending", new_count)
m3.metric("🟩 Prepress Ready", ready_count)
m4.metric("🟥 On Hold", hold_count)
m5.metric("⚡ Agent Processed", f"{pct}%")

st.progress(pct / 100 if total else 0, text=f"{processed} of {total} jobs handled")
st.divider()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
filter_col, _ = st.columns([1, 3])
with filter_col:
    status_filter = st.selectbox(
        "Filter",
        ["All", "new", "prepress_ready", "Hold - Customer Service"],
        index=0,
    )

if status_filter == "All":
    visible = jobs
elif status_filter == "Hold - Customer Service":
    visible = [j for j in jobs if is_hold(j.get("status", ""))]
else:
    visible = [j for j in jobs if j.get("status") == status_filter]

st.subheader(f"📋 Active Jobs Queue ({len(visible)})")

if not visible:
    st.info("No jobs match the selected filter.")
    st.stop()

# ---------------------------------------------------------------------------
# Job cards
# ---------------------------------------------------------------------------
def status_pill(status: str) -> str:
    if status == "prepress_ready":
        return "<span class='status-pill status-ready'>🟩 PREPRESS READY</span>"
    if is_hold(status):
        return "<span class='status-pill status-hold'>🟥 ON HOLD</span>"
    if status == "new":
        return "<span class='status-pill status-new'>🟦 NEW</span>"
    return f"<span class='status-pill status-other'>{status.upper()}</span>"


def yn(val) -> str:
    if val is True:
        return "✅ Yes"
    if val is False:
        return "❌ No"
    return "—"


for job in visible:
    specs = job.get("specs", {}) or {}
    meta = job.get("file_metadata", {}) or {}
    status = job.get("status", "unknown")

    with st.container(border=True):
        head_l, head_r = st.columns([3, 1])
        with head_l:
            st.markdown(
                f"<p class='job-id'>📄 {job.get('order_id', '?')}</p>"
                f"<p class='customer'>{job.get('customer_name', '—')}</p>",
                unsafe_allow_html=True,
            )
        with head_r:
            st.markdown(
                f"<div style='text-align:right;'>{status_pill(status)}</div>",
                unsafe_allow_html=True,
            )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Product", specs.get("product", "—"))
        c2.metric("Dimensions", specs.get("dimensions", "—"))
        c3.metric("Stock", specs.get("stock", "—"))
        c4.metric("Resolution", f"{meta.get('resolution_dpi', '—')} DPI")

        d1, d2, d3 = st.columns(3)
        d1.markdown(f"**Color Space:** `{meta.get('color_space', '—')}`")
        d2.markdown(f"**Bleed Required:** {yn(specs.get('bleed_required'))}")
        d3.markdown(f"**Has Bleed:** {yn(meta.get('has_bleed'))}")

        notes = (job.get("agent_notes") or "").strip()
        if notes:
            st.markdown(
                f"<div class='notes-box'>🤖 <b>Agent Notes:</b> {notes}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div class='notes-empty'>Awaiting agent analysis…</div>",
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# Auto-refresh trigger (placed last so the page fully renders before reruns)
# ---------------------------------------------------------------------------
if auto_refresh:
    time.sleep(interval)
    st.rerun()
