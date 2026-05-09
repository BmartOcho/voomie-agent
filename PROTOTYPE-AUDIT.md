# Prototype Audit — PressFlow AI vs Voomie SPEC v2

Read-only audit of the May 7 prototype at the working-dir root. Determines, file by file, what carries forward to Voomie and what doesn't.

## Top-line judgment

The prototype solves a **different problem** than Voomie. PressFlow AI assumes a web-to-print storefront has already produced **structured** job records in MongoDB (with `specs.product`, `file_metadata.color_space`, `bleed_required`, etc.) and the agent's job is to **validate them against a rulebook**. Voomie's core value is the part that produces those structured records in the first place — turning a messy human email into a `shoptalk` declaration. PressFlow's downstream is Voomie's upstream, not its predecessor.

That said, four pieces of infrastructure lift cleanly: MongoDB connection patterns, the Streamlit dashboard skeleton, the FastMCP/stdio scaffold, and the Vertex AI function-calling loop. The prototype's reasoning logic, schema, branding, and rulebook all get replaced.

**One uncomfortable observation up front:** in the prototype, the MCP server (`mcp_server.py`) is **not actually invoked by the agent**. `run_agent.py` re-declares the same tools as Vertex `FunctionDeclaration`s and accesses MongoDB directly. The MCP layer is decoration today. v2's DoD line "MCP server actually invoked over stdio — no decoration" reads, in this light, as an explicit course-correction. The MCP-to-Vertex bridge is the missing piece, not boilerplate.

---

## Per-file audit

### `app.py` — Streamlit dashboard (251 lines)

**Classification: (b) structurally salvageable but needs significant rework**

**Maps to:** CSR dashboard (the "User and trigger" surface in SPEC v2 §User and trigger and §Interaction model).

**What lifts cleanly:**
- Streamlit polling-and-render pattern with auto-refresh toggle + interval slider
- MongoDB connection helper (`@st.cache_resource` client + ping check + error surfacing)
- Status-pill CSS pattern (`.status-pill`, color variants)
- Top-line metrics row with 5 columns (Total / New / Ready / Hold / % processed) — directly transferable to Voomie's phase counts
- Sidebar controls block, filter dropdown
- Job-card container pattern (`st.container(border=True)` + header row + metric grid + notes box)

**What needs rework:**
- **Schema is wrong end-to-end.** The dashboard reads `order_id`, `customer_name`, `specs.{product,dimensions,stock,bleed_required}`, `file_metadata.{color_space,resolution_dpi,has_bleed}`, `agent_notes`. v2's jobs schema has `_id` (J-number), `customer_id` (reference, not embedded name), `phase`, `declaration_source`, `action_plan`, `attachments_metadata`, plus a separate `conversations` collection. Every field reference in the card rendering changes.
- **Status taxonomy doesn't match.** Prototype: `new` / `prepress_ready` / `Hold - Customer Service`. v2: dashboard-visible variants of `Drafting/Validating/Done/Escalated` plus `phase` substeps within Drafting. The pills, filter options, and the `is_hold()` helper all need rewriting.
- **The card body has the wrong content.** Prototype shows product/dimensions/stock/DPI/color-space/bleed — i.e., validation outputs. Voomie cards need to show: current phase (streaming), declaration source preview, the latest draft `agent_to_customer` reply (if any) with a "send" button, attachment list, J-number parent/child grouping for multi-job, and the conversation transcript expandable. That's a substantially different card.
- **Branding.** "PressFlow AI" / 🖨️ / "Prepress Coordinator Dashboard" → Voomie / its own visual identity.
- **No support for the 2-second-first-update target.** Polling at 1-15s intervals is too coarse. v2 needs either a tighter poll (1s minimum) or a change-stream/websocket path. Auto-refresh polling at 1s is fine for the demo; flag if production-grade is wanted.

**Estimated rework:** medium. The skeleton (polling, layout, CSS, MongoDB plumbing) is ~30% of the file and lifts. The card body and schema bindings (~70%) are rewritten. Net: ~150–180 lines of new dashboard code.

---

### `mcp_server.py` — FastMCP server (35 lines)

**Classification: (b) structurally salvageable but needs significant rework**

**Maps to:** v2 §MCP tool surface (the entire MCP server module).

**What lifts cleanly:**
- `from mcp.server.fastmcp import FastMCP` + `FastMCP("name")` instantiation
- `@mcp.tool()` decoration pattern
- `mcp.run()` stdio entry point
- The "two-tool minimum viable MCP server" shape

**What needs rework:**
- v2 specifies **11 MCP tools** (lookup_customer, create_customer, inspect_pdf, query_stock_registry, query_press_registry, parse_shoptalk, render_preview, update_job_status, append_conversation_turn, persist_job, flag_for_human). The prototype defines 2.
- The 2 existing tools (`get_new_jobs`, `update_job_status(order_id, new_status, notes)`) are **not** v2's tools. Prototype's `update_job_status` writes status + notes; v2's same-named tool writes a `phase` string for streaming progress. Different signatures, different semantics.
- The connection string is hardcoded inline — v2 should pull from env (and the prototype's `.gitignore` already covers `.env*`, so the hygiene exists, just isn't wired up).
- Subprocess calls to the Racket parser (`parse_shoptalk`) and Python verifier (`render_preview`) are entirely new — no analog in the prototype.

**The decoration problem:** as noted in the top-line, this server isn't on the critical path of the prototype's agent. It exists as a parallel implementation. Voomie's runtime needs to actually invoke this over stdio — that's a build-time integration question, not a code-reuse question.

**Estimated rework:** the file as written gets replaced. The FastMCP pattern is the only thing that survives, and that's ~3 lines.

---

### `run_agent.py` — Vertex AI Gemini agent loop (231 lines)

**Classification: (c) solving a different problem — but with reusable infrastructure pockets**

**What made it diverge:** the prototype's loop is "fetch validated jobs → apply 4 rules → update status." Voomie's loop is the 10-step mission in SPEC §Multi-step mission: classify, identify customer, inspect attachments, resolve specs, check coatings, validate dates, batch clarifications, parse declaration, persist. The system instruction, the conversational pattern (Voomie has multi-turn back-and-forth with the customer; prototype is single-shot), the tool surface, and the success criteria are all different.

**Reusable infrastructure (lifts as patterns, not as code):**
- `vertexai.init(project=..., location=...)` boilerplate
- `FunctionDeclaration` + `Tool(function_declarations=[...])` registration pattern
- `GenerativeModel("gemini-2.5-flash", system_instruction=..., tools=...)` setup
- `model.start_chat()` + manual function-call dispatch loop pattern
- `get_collection()` retry helper with `serverSelectionTimeoutMS=5000` + ping check (good resilience pattern)
- `safe_send()` wrapper for chat errors
- The "send tool result back as `Part.from_function_response(...)`" pattern
- `KeyboardInterrupt` + fatal-error try/except at the entry point

**Important gap:** the prototype does **not** invoke MCP-over-stdio. It mirrors the MCP tools as Vertex function declarations and calls MongoDB directly. v2's DoD requires the agent to actually go through stdio MCP. That bridge — translating Vertex function calls into MCP stdio calls and routing responses back — is the implementation risk I flagged at sign-off. None of it exists in the prototype.

**What gets replaced:**
- The system instruction (4-rule prepress rulebook → Voomie's 10-step extraction mission + coating-conflict rules + clarification protocol)
- The two-tool dispatch loop → 11-tool dispatch loop with phase-streaming and conversation-turn appending interleaved
- Project ID `pressflow-hackathon` → whatever Voomie's GCP project is
- Single-shot chat → multi-turn conversation state machine (Drafting/Validating/Done/Escalated) with the 3-turn cap

**Estimated rework:** the file gets rewritten. The retry/safe-send patterns survive (~30 lines). System instruction is new. The dispatch loop expands ~3×. Net: this is a from-scratch agent module that borrows error-handling conventions.

---

### `seed_db.py` — DB seeder (32 lines)

**Classification: (c) solving a different problem**

**What made it diverge:** seeds `active_jobs` with 2 records in the validation schema (`order_id`, `specs`, `file_metadata`, status `new`). v2 needs to seed five collections: `customers` (5 records), `jobs` (multi-job-per-customer history), `conversations`, `flags`, and `seed_history`. Different shape, different volume, and an explicit DoD requirement that the seeded data be PII-scrubbed and grep-verified.

**What lifts:** the "wipe-then-insert" technique. That's three lines of pymongo. Calling it boilerplate is generous.

**Net:** rewritten. The new `seed_db.py` is a meaningfully larger script (5 collections, 5 anonymized customers, multiple historical jobs each, conversation transcripts, plus the PII grep step the DoD asks for in the README).

---

### `test_db.py` — connection smoke test (35 lines)

**Classification: (d) infrastructure boilerplate that lifts cleanly**

Generic "connect to Atlas, insert a row, read it back, print result" test. The connection string and the dummy record change; the structure stays. Voomie can keep this as-is for first-time setup verification, just swap the dummy job for a Voomie-shaped one.

---

### `README.md` — PressFlow AI README (206 lines)

**Classification: (c) solving a different problem**

The substance sells a different product (rule-validation agent for pre-structured jobs) and frames the MongoDB MCP integration as the signature move when in fact the prototype doesn't actually route through MCP. The README is **more aspirational than the code**, which is something Voomie's DoD ("README is honest about scope") is explicitly designed to avoid.

**What's structurally reusable:**
- The "Built for the MongoDB Track" framing (still applies)
- The architecture diagram **placement** (Voomie's diagram from SPEC §Architecture diagram drops in)
- The "Quick Start (for Judges)" section template — good UX for a hackathon README
- The badges/license footer
- The repo-layout block (rewritten with Voomie's files)
- The technology table (replaced row by row)

**What gets rewritten:** the problem statement, solution description, prepress rulebook section, "Why This Matters" pitch, and the prototype-vs-production honesty section that v2's DoD asks for but isn't present in the prototype README.

**Estimated rework:** ~70% rewrite, 30% structural template reuse.

---

### `requirements.txt`

**Classification: (d) infrastructure boilerplate that lifts cleanly**

Has the right four foundations (`pymongo`, `streamlit`, `google-cloud-aiplatform`, `mcp`). Voomie additions:
- `pikepdf` (for `inspect_pdf` MCP tool)
- Whatever Python deps the verifier needs (PDF rendering — likely `reportlab` or similar; depends on how shoptalk's verifier is structured)
- Possibly a date-parsing library if Voomie does anything fancier than `datetime` for the deadline-validation step
- Racket isn't a Python dep; the parser is invoked via subprocess

No version conflicts expected. Append, don't rewrite.

---

### `.gitignore`

**Classification: (d) infrastructure boilerplate that lifts cleanly**

Sensible Python/Streamlit/Mac/secrets coverage. Notably already excludes `service-account*.json` and `gcloud-key*.json` — correct for Vertex AI. Voomie can use as-is. Maybe add `*.pdf` for any uploaded customer attachments stored locally during dev (or store them in a designated dir that's already ignored).

---

### `LICENSE`

**Classification: (d) infrastructure boilerplate that lifts cleanly**

MIT, no changes needed. Hackathon license requirement (per v2 DoD: "public repo with detectable open-source license") is satisfied by this file as-is.

---

## Cross-cutting observations

1. **The MCP server is decoration in the prototype.** This is the single biggest gap between the prototype's claims and what's actually executed. Voomie's DoD line about no-decoration MCP exists for a reason — the bridge work is real and unbuilt.

2. **Schema overlap is near-zero.** Field names, collection names, document shapes, and status taxonomies all change. This makes "merge in place" much harder than "build fresh, port what's reusable." The schema break also means the seeder and the dashboard can't be incrementally migrated; they break together.

3. **Branding is throughout the codebase.** "PressFlow AI" appears in every file's docstring, page title, system instruction, and README copy. Voomie rename is a global find-and-replace, not a one-line change.

4. **What's actually salvageable, in priority order:**
   - **High value:** Vertex AI initialization + function-calling dispatch pattern, MongoDB resilience helpers (`get_collection` retry, `safe_send`), `.gitignore`, `LICENSE`, `requirements.txt` foundation, Streamlit dashboard skeleton (auto-refresh, status pills, MongoDB polling), README quick-start template
   - **Medium value:** FastMCP scaffold pattern (3 lines), test_db.py structure
   - **Low value (replaced):** prepress rulebook system instruction, two-tool MCP server contents, validation-schema seed data, dashboard card body, README substance

5. **Recommended path forward:** option (1) from the sign-off discussion — `voomie/` as the new clean repo, root files moved to `prototype-v0/` (or just left at root and `voomie/` becomes the build target). Reasoning: schema break is total, branding is pervasive, and the MCP-decoration problem is structural. Trying to mutate the prototype in place will fight you on every file; starting clean and pulling in the four reusable infrastructure patterns takes less time than refactoring 500+ lines around a different schema and a different agent task.

6. **Estimate.** From this prototype to a v2-DoD-compliant Voomie, with the patterns above pulled in: ~80% new code, ~20% lifted patterns. The lifted patterns save real time on Vertex/MongoDB plumbing and Streamlit layout, just not on the agent loop or schema work.
