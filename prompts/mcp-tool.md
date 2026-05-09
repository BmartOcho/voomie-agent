Build two new MCP tools wrapping shoptalk's registry-query CLI, following the established pattern from tools/parse_shoptalk_server.py.
Pre-conditions (verify before starting):

shoptalk repo at ~/Desktop/shoptalk (or $SHOPTALK_REPO_PATH) has tools/query-registry.rkt (or wherever the shoptalk team placed it — confirm by reading shoptalk's MEMORY.md or by listing the tools/ directory).
shoptalk's pytest suite for the registry CLI passes locally. Run it (cd ~/Desktop/shoptalk && pytest tests/test_registry_cli.py or whatever the path is) and confirm green before building against it.
Read shoptalk's REGISTRY-RECON.md and the implementation prompt that was sent to shoptalk's CC (saved in Voomie's prompts/ folder, or paste it into the conversation if not). The JSON contract is locked; this prompt assumes you've internalized it.

If any pre-condition fails, stop and report. Do not build against an incomplete shoptalk side.
Files to create:

tools/registry_server.py — a single MCP server exposing two tools:

query_stock_registry(criteria: dict, limit: int = 3) -> dict
query_press_registry(criteria: dict, limit: int = 3) -> dict

Both tools wrap a single subprocess invocation of racket tools/query-registry.rkt (with cwd set to SHOPTALK_REPO_PATH), passing a JSON batch of one query on stdin. The MCP tool receives one query at a time from Gemini; batching across multiple Gemini tool calls is out of scope for this implementation (documented as future work — see "Future amortization" below).
Return shape on success: {ok: true, kind: "stock"|"press", count: int, results: [...records...]} — i.e., the per-query envelope from shoptalk's CLI output, with ok: true prepended for consistency with parse_shoptalk's success shape.
Return shape on error: {ok: false, error_class: str, message: str, query_index: int|null, exit_code: int} — same discriminator pattern as parse_shoptalk. The query_index field comes from shoptalk's error JSON when applicable.
FunctionDeclaration descriptions for Gemini. This is the part that does real work — recall from the bridge POC that the FunctionDeclaration description drove Gemini's correct behavior on the flat-card vs postcard choice, with no system prompt help. The descriptions matter.
For query_stock_registry, the description must explicitly tell Gemini:

This tool searches the print shop's actual stock inventory. Use it instead of guessing what stocks exist.
Approximate or fuzzy customer language ("something like 80# cover," "the heavy gloss stock," "around 100lb") goes through the text_search criterion. Do not parse fuzzy language yourself; pass it through.
Precise stock specifications ("100# Gloss Cover," "16pt C2S") also go through text_search — the registry handles both fuzzy and precise matching internally.
Structured filters (basis_weight_min, basis_weight_max, coating, finish) are for narrowing — combine them with text_search only when the customer specifies multiple constraints.
The match_tier field on each result indicates how the match was found: "exact" and "alias" are confident matches; "ambiguous" means multiple candidates resolved equally and the agent should ask the customer to disambiguate; "name-substring", "alias-substring", and "token-overlap" are fuzzy fallbacks the agent should treat as suggestions.
Default limit is 3. Use higher limits when offering options to the customer; use 1 when the agent is confident about the resolution.

For query_press_registry:

This tool searches the print shop's actual press inventory.
Format filter (format: "sheet" | "wide-format" | "any") is for narrowing by press category.
text_search resolves press names, aliases, and shortnames.
Same match_tier semantics.

These descriptions should be in the FunctionDeclaration description field directly — Gemini reads them to decide when and how to call the tools. Do not put this guidance in a system prompt; the bridge POC proved tool descriptions are the right surface for tool-routing decisions.
Criteria translation. The MCP tool receives criteria as a Python dict from Gemini (typed via the FunctionDeclaration parameter schema). Pass it through to shoptalk's CLI as a JSON batch with one query, like so:
pythonbatch = {"queries": [{"kind": "stock", "criteria": criteria, "limit": limit}]}
proc = subprocess.run(
    [RACKET_BIN, "tools/query-registry.rkt"],
    input=json.dumps(batch),
    cwd=SHOPTALK_REPO_PATH,
    capture_output=True,
    text=True,
    timeout=10,
)
Parse stdout as JSON. On success (exit_code == 0), return the first envelope from result["results"] with ok: true prepended. On error (exit_code != 0), parse the error JSON and return ok: false with the relevant fields.
Logging. Match the verbosity pattern from parse_shoptalk_server.py. Stage prints at: subprocess invocation start, raw stdout/stderr received, parse success/failure. The agent's runtime log is the demo's debugging surface — don't go silent.
Subprocess cleanup. parse_shoptalk_server.py already establishes the timeout/exception handling pattern. Reuse it. Same try/finally shape, same cleanup. If you find yourself diverging significantly, stop and ask why.

Files to modify:

lib/mcp_bridge.py — extend if needed to support multi-tool MCP servers (one server, two tools). The current bridge spawns one server per script. If registry_server.py exposes both query_stock_registry and query_press_registry from a single FastMCP process, confirm the bridge dispatches Vertex function calls to the right tool name within that server. FastMCP advertises its tool list on initialization, so this should work without bridge changes — but verify, don't assume.
If bridge changes are needed, they should be minimal — adding tool-name routing on top of the existing server-routing logic. Do not refactor the bridge for elegance during this work; the bridge has been proven and shouldn't drift.

Tests — tests/test_registry_bridge.py:
Pytest suite covering both tools end-to-end through MCP, real subprocess to Racket, no mocks. Each test starts the MCP server, runs the test, tears down. Mirror the structure of test_parse_shoptalk_bridge.py exactly — same setup pattern, same teardown, same assertion style.
Required test cases:

Stock query with no criteria returns up to limit results with ok: true, kind: "stock", count > 0.
Stock query with structural criteria (basis_weight_min: 80, coating: "coated") returns only matching stocks. Spot-check at least one returned record's weight and coated fields.
Stock query with text_search: "100 gloss cover" returns the canonical record for the 100-gloss-cover alias as the top result. Cross-reference: parse a postcard declaration via parse_shoptalk that uses 100-gloss-cover as the stock alias, extract the resolved PrintIQ code from the action plan, confirm the registry query's top result has the matching code. This is the parity test on the Voomie side — it confirms that what the bridge returns and what the parser resolves agree.
Press query with format: "sheet" returns only sheet-fed presses (assert family field on returned records).
Empty result set for a query that should match nothing (e.g., basis_weight_min: 9999) returns cleanly with ok: true, count: 0, results: []. Not an error.
Unknown criteria key (e.g., {"weight_in_kg": 50}) returns ok: false, error_class: "criteria-error".
Criterion-not-valid-for-kind (e.g., calling query_press_registry with coating: "coated") returns ok: false, error_class: "criteria-error".
limit: 1 with a tier-1 match returns exactly one result with match_tier: "exact" or "alias". limit: 5 with a tier-1 hit returns no more than the tier-1 results — confirm no top-up from lower tiers.
Ambiguous text_search (find an alias in shoptalk's stock data that resolves to multiple records) returns multiple results all tagged match_tier: "ambiguous".
Subprocess cleanup — confirm the Racket subprocess terminates after each call and no zombies accumulate. (Implementation: a test that runs 5 queries in sequence and asserts process count returns to baseline after each.)

Demo POC script — scripts/poc_registry.py:
A standalone script analogous to poc_bridge.py that demonstrates Gemini using the registry tools end-to-end. Hardcoded test message:

"Hi, I need to know if you guys carry something like 80# matte cover stock for a postcard run. The customer wants something that feels heavy and premium but not glossy. What do you have?"

Show Gemini reasoning through this, calling query_stock_registry with text_search: "matte cover" plus structural filters, getting candidates back, and either selecting one or asking a clarifying question if the results are ambiguous. Print the full round-trip in the same staged format as poc_bridge.py — Vertex initialized, MCP server spawned, Gemini's tool calls with arguments, MCP responses, Gemini's final reply.
This POC is the second proof point that the bridge generalizes and that tool descriptions drive Gemini's behavior. The demo recording will eventually use this same pattern at higher fidelity.
Constraints (same as parse_shoptalk POC):

Real subprocess, real Racket, no mocks. The whole point is the bridge generalizes.
SHOPTALK_REPO_PATH env var with default ~/Desktop/shoptalk, same as the parse server.
RACKET_BIN env var with default matching the existing parse_shoptalk_server.py.
Subprocess cleanup must be airtight. The parse_shoptalk_server.py and poc_bridge.py patterns are the reference.
Do not modify parse_shoptalk_server.py, poc_bridge.py, or test_parse_shoptalk_bridge.py. They are working — leave them alone.

Future amortization (out of scope for this implementation, document in code comments):
Multiple Gemini tool calls within one conversation currently each spawn a fresh Racket subprocess, paying ~250-310ms cold-load each. Future work could amortize this either by (a) batching at the MCP server layer (collect tool calls within a short window, flush as one Racket invocation) or (b) keeping a long-lived Racket REPL process. Trigger condition: if a single agent turn issues >5 registry queries and the demo's 90-second target is at risk, revisit. Document this in a comment at the top of registry_server.py.
Stop after:

tools/registry_server.py exists and exposes both tools
tests/test_registry_bridge.py exists and all 10 tests pass against real shoptalk subprocess
scripts/poc_registry.py runs end-to-end and Gemini demonstrably uses the registry to handle the matte-cover inquiry
Console output of a successful POC run is captured and reported

Report results when complete.