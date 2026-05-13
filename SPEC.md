# Voomie: Hackathon Spec v1

## What it is

Voomie is a natural-language front door to shoptalk. Customer messages arrive in plain English, often with PDF attachments. Voomie reads them, inspects attachments, asks at most one batched round of clarifying questions per ambiguity, produces a valid shoptalk declaration, and writes a complete job record to MongoDB. A human CSR picks up from there for pricing and final approval.

Voomie does not replace the CSR. Voomie produces a clean, machine-readable spec from messy human input — the part of the CSR's job that currently consumes the most time and produces the most errors. Voomie is async by design. The CSR pastes a message and moves on; Voomie surfaces the job when it's ready for review.

## User and trigger

The user is the CSR-facing dashboard at the shop. Trigger is a new customer message arriving — pasted into the dashboard or seeded as a fixture for the demo. Job type for v1 is postcard only, with one pre-baked booklet declaration shown end-to-end through the verifier as the closing demo beat.

## Interaction model: async by default

Voomie returns control to the CSR immediately on message receipt. Status updates stream to the dashboard as Voomie progresses. The CSR sees jobs move through human-readable phases — reading message, checking attachments, looking up customer, resolving stocks and presses, drafting reply, validating spec, ready for review — without ever blocking on Voomie's work.

Two latency targets:

- **First visible status update within 2 seconds of message receipt.** This is the responsiveness target — the CSR must see Voomie working immediately, or Voomie feels broken.
- **Ready-for-review within 90 seconds in the demo case.** This is the throughput target — the recording stays tight and the demo doesn't drag.

When a job transitions to ready for review or clarification needed, the dashboard surfaces it visually (badge, row highlight, animation). Production would add desktop notifications or Slack pings — called out as next-step, not built for v1.

## Multi-step mission

For each incoming message, Voomie runs this loop:

1. **Acknowledge and classify.** Create job record(s) in MongoDB immediately. Extract job count from the message. Single-job → one declaration. Multi-job → split into J-number children (J123456-01, J123456-02) under one parent customer record, processed sequentially. Sequential rather than parallel to keep multi-job demo legible; parallel is post-v1.
2. **Identify customer.** Match on email or name against MongoDB customer collection via `lookup_customer`. If known, retrieve recent job history. If new, create a customer record.
3. **Inspect attachments.** If PDFs are attached, call `inspect_pdf` to extract trim size, page count, color space, embedded fonts, and bleed presence. Use those facts to inform the declaration. If non-PDF formats are attached (JPG, PNG, AI, INDD), recognize them and ask the customer for a PDF. Voomie does not attempt format conversion.
4. **Resolve specs.** Query the stock and press registries via `query_stock_registry` and `query_press_registry`. Both tools accept fuzzy criteria and return ranked candidates with a limit parameter. Voomie passes through approximate language ("something like 80# cover") directly; the registry handles fuzzy matching, not Voomie. If a stock has no acceptable match, Voomie does **not** escalate. The default behavior is to draft a customer-facing clarification that names the closest available alternatives, asks for a supplier reference if special-order is desired, and surfaces the cost/timeline implication. Escalation via `flag_for_human` is reserved for terminal states only (see step 7).
5. **Check coatings.** Apply Voomie's prompt-level compatibility rules. Flag genuine conflicts (e.g., foil over laminate — the foil won't adhere to the film) in the conversation and ask the customer which intent to honor. Compatible-but-order-dependent combinations (e.g., spot UV over soft-touch laminate, which is a premium finish when laminate is laid down first) should be encoded in the declaration with the correct production order, not flagged. Compatible-but-production-sensitive combinations (e.g., spot UV on uncoated stock, which absorbs the coating and needs a flood seal pass first; soft-touch on uncoated stock, which covers the tactile feel the customer may have specifically chosen) should be noted in the declaration and confirmed with the customer where intent is ambiguous. shoptalk has no opinion here, so Voomie owns this judgment. The compatibility rules live in Voomie's system prompt for v1; production would externalize to `coating_rules.yaml`. Called out in README as a known prototype shortcut.
6. **Validate dates.** If a deadline is mentioned, resolve to a real calendar date relative to today. Sanity-check it isn't in the past or absurdly far out. Populate `due:` and `rush:` in the declaration. shoptalk does not validate dates today (passthrough fields), so Voomie owns this check.
7. **Clarify first; escalate only as terminal state.** The default response to any ambiguity Voomie can't resolve itself is to draft one batched round of clarifying questions per ambiguity (at most one customer-facing turn per ambiguity). The 3-turn cap is the conversational backstop. **`flag_for_human` is reserved for terminal states only:**

    - (a) the 3-turn customer-facing cap is exhausted without resolution,
    - (b) `parse_shoptalk` fails after one self-correction attempt (see step 9),
    - (c) an MCP tool returns an infrastructure failure that exceeds the retry budget (e.g., MongoDB unavailable across all retries), or
    - (d) Voomie hits an unrecoverable internal error.

    When flagging, Voomie emits the partial declaration with `// HUMAN_REVIEW_NEEDED:` comments (shoptalk warns-but-accepts) and calls `flag_for_human` with a structured reason. Anything resolvable conversationally — missing specs, unfamiliar or special-order stocks, coating ambiguities, out-of-scope sub-requests — is a clarify-first path, not a flag path. This is the deliberate "don't bother the CSR until you have to" policy: humans get pulled in only when conversation has run out of room, not at every friction point.
8. **Acknowledge out-of-scope.** If the customer requested mailing services, list management, design work, or anything else outside shoptalk, capture those as sub-requests on the job record without attempting to spec them. The CSR sees them when they pick up the job.
9. **Validate declaration.** Send the draft shoptalk source through `parse_shoptalk`. If it parses cleanly, persist. If it fails, log the structured error and attempt exactly one self-correction. If the second attempt also fails, this is a terminal state per step 7(b) — emit the partial declaration with `// HUMAN_REVIEW_NEEDED:` comments and call `flag_for_human` with the parser error as the structured reason.
10. **Persist and notify.** Final job record contains: customer reference, declaration source, action plan s-expression, attachment metadata, conversation log, out-of-scope notes, due date, rush flag, and any human-review flags. Status transitions to ready for review or clarification needed; dashboard surfaces it.

## MCP tool surface

All tools exposed through the MCP server, actually invoked over stdio. No tool is decorative.

- `lookup_customer(query)` — returns customer record + recent jobs from MongoDB
- `create_customer(record)` — creates new customer record
- `inspect_pdf(path)` — pikepdf-backed; returns trim size, pages, color space, fonts, bleed presence
- `query_stock_registry(criteria, limit=3)` — wraps shoptalk's stock registry; fuzzy matching internal to the tool; returns ranked candidates
- `query_press_registry(criteria, limit=3)` — same for presses
- `parse_shoptalk(source)` — invokes the Racket parser via subprocess; returns action plan s-expression on success or structured errors on failure
- `render_preview(action_plan)` — invokes the Python verifier; returns preview PDF (used in the booklet closer)
- `update_job_status(job_id, phase)` — streams human-readable phase updates to the dashboard
- `append_conversation_turn(job_id, turn)` — incremental conversation log persistence; survives mid-flow crashes
- `persist_job(job_record)` — writes final job record to MongoDB. Side effect: also updates `customer.last_seen` and appends conversation-derived notes to `customer.shop_relationship_notes`. Documented explicitly so the side effect is intentional, not surprising.
- `flag_for_human(job_id, reason, context)` — surfaces job in CSR queue with full context

Customer-facing replies are written to the conversation log as `role: "agent_to_customer", status: "draft"` turns via `append_conversation_turn`. The dashboard surfaces them as "needs CSR review before sending." This is the human-in-the-loop checkpoint — Voomie drafts, CSR sends.

## MongoDB schema

Five collections.

- **customers** — `{_id, name, email, phone, shop_relationship_notes, first_seen, last_seen}`
- **jobs** — `{_id: J-number, parent_id (for multi-job children), customer_id, status, phase, declaration_source, action_plan, attachments_metadata, out_of_scope_notes, due_date, rush, created_at, updated_at}`
  - `declaration_source` is the shoptalk source string Voomie produced (`#lang shoptalk\npostcard\n  ...`)
  - `action_plan` is the s-expression the parser emits, stored as text for inspection by the verifier
- **conversations** — `{_id, job_id, messages: [{role, content, timestamp, attachments, status}]}` — full transcript including Voomie's reasoning and draft replies
- **flags** — `{_id, job_id, type, reason, context, resolved}`
- **seed_history** — your real customer job history, anonymized, used for few-shot prompting and the "you've ordered this before" demo beat

## Conversation flow as state machine

Three states. Transitions deterministic.

- **Drafting.** Voomie has the message and any attachments, gathering specs. Can ask up to one batched round of clarifying questions per ambiguity. Loops to itself on customer reply. Hard cap: 3 customer-facing turns.
- **Validating.** Voomie has a candidate declaration, calling `parse_shoptalk`. On parse success → Done. On parse failure → one self-correction attempt → either Done or Escalated.
- **Done or Escalated.** Job persisted with final status. Dashboard surfaces it.

Voomie never loops indefinitely. The 3-turn cap is hard.

## Demo arc (3 minutes)

- **0:00–0:20** — You on camera in the shop. "I work prepress at a commercial printer. Customer messages look like this." Show one of the four real messages on screen.
- **0:20–1:50** — Live Voomie run on Chris's message. Dashboard already shows 2-3 in-flight jobs at various phases (texture). Paste Chris's message; J123456-01 and J123456-02 appear immediately, status moving. Registry resolution flashes by as a fast cut — don't narrate it. Pause on the production-order encoding for spot UV + soft touch laminate. This is the moment Voomie demonstrably knows something the customer didn't say — give it the beat. Show Voomie's draft clarifying question, CSR reviews and approves, simulated customer reply lands, Voomie completes both declarations, persists. Dashboard updates in real time.
- **1:50–2:30** — Switch to the pre-baked booklet declaration. "Voomie's value isn't just clean specs — it's that downstream tools can act on them." Run booklet through `render_preview`, show the rendered PDF. This is the "and here's what infrastructure looks like" beat.
- **2:30–2:55** — Architecture flash: Voomie + MCP + MongoDB + shoptalk + verifier. Five boxes, one diagram. Mention partner integration (MongoDB) is load-bearing.
- **2:55–3:00** — "Voomie and shoptalk are open source. Repo's linked. Thanks."

The Frank/mailing message and Message 4's attachment-driven case are documented in the Devpost writeup as additional handled cases but not shown on video — three minutes is too short for four messages.

## Definition of done

- Voomie handles all four real customer messages end-to-end, with escalation behavior counting as success when escalation is the correct response
- MCP server actually invoked over stdio — no decoration
- MongoDB stores complete job records, conversations, flags, and seeded history
- shoptalk parser invoked via subprocess for every declaration; structured errors surfaced
- pikepdf MCP tool inspects real PDF attachments
- Verifier produces a real preview PDF for the booklet closer
- 5 customers seeded in MongoDB (real anonymized data); demo features 1 returning customer for the "ordered before" beat; dashboard shows multiple jobs for texture
- PII verification: grep across seed files confirms no original customer names, emails, domains, or addresses remain. Documented in README.
- First visible status update within 2 seconds of message receipt
- Ready-for-review within 90 seconds in the demo case
- Self-correction loop tested with at least one deliberately-malformed declaration fixture; recovery path verified
- 3 dry-runs of the Chris demo completed before recording day; one good run captured as fallback recording in case live demo breaks during recording
- README is honest about scope: prototype vs. production, what shoptalk does separately, known shortcuts (coating rules in prompt, no eval harness, etc.)
- 3-minute demo video; public repo with detectable open-source license at top of About section; Devpost form complete

## Out of scope for v1 (explicit)

- Pricing — handed to MIS/CSR
- Mailing list services — captured as sub-requests, not specced
- Design work — same
- Job types other than postcard (booklet shown via canned declaration only)
- File format conversion (JPG → PDF, etc.) — Voomie asks for PDF
- Live customer chat — Voomie processes captured messages
- Press scheduling, MIS integration, web-to-print portal connection — real shop systems Voomie could eventually feed; none in scope here
- Multi-tenancy, auth, RBAC — single-shop demo
- Eval harness / golden output verification — declaration validity ≠ declaration correctness; flagged as next-step, not built. Tested only on the four real fixtures.
- External coating compatibility rules file — rules live in Voomie's prompt for v1; production would externalize.

## Architecture diagram (for the README and the demo flash card)

```
  Customer message + attachments
              │
              ▼
       ┌────────────┐
       │   Voomie   │  (Gemini via Vertex AI)
       │   agent    │
       └─────┬──────┘
             │ MCP over stdio
             ▼
   ┌─────────────────────┐
   │     MCP server      │
   └─┬─────┬─────┬─────┬─┘
     │     │     │     │
     ▼     ▼     ▼     ▼
 MongoDB pikepdf shoptalk verifier
 (jobs,   (PDF   (Racket  (preview
 history, inspect) parser) PDF)
 logs)
```

CSR dashboard reads from MongoDB, writes nothing back except CSR-approved sends and overrides.
