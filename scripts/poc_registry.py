"""
poc_registry.py — End-to-end proof: Vertex AI Gemini ↔ MCP stdio ↔ Racket
                  registry CLI.

Hardcoded test: a fuzzy customer inquiry about an 80# matte cover stock
that needs to be resolved against the print shop's actual inventory.

The script:
  1. Initializes Vertex AI (Gemini 2.5 Flash) with two tools:
     query_stock_registry and query_press_registry.
  2. Spawns the registry MCP server as a subprocess (stdio JSON-RPC).
  3. Sends the customer message to Gemini.
  4. Dispatches Gemini's tool calls (by name) through the same MCP bridge.
  5. Routes registry responses back to Gemini.
  6. Prints the final Gemini response — which should either select a
     specific stock or ask a clarifying question if the candidates are
     ambiguous.

This is the second proof point that the bridge generalizes (one server,
two tools, multi-turn dispatch) and that FunctionDeclaration descriptions
drive Gemini's tool-routing decisions without system-prompt help.

Env vars (all optional, sensible defaults applied):
  GCP_PROJECT_ID       Vertex AI project       (default: pressflow-hackathon)
  GCP_REGION           Vertex AI region        (default: us-central1)
  SHOPTALK_REPO_PATH   shoptalk repo path      (default: ~/Desktop/shoptalk)
  RACKET_BIN           Racket binary           (default: /Applications/Racket v9.1/bin/racket)

Run:
  python scripts/poc_registry.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import vertexai  # noqa: E402
from vertexai.generative_models import (  # noqa: E402
    FunctionDeclaration,
    GenerativeModel,
    Part,
    Tool,
)

from lib.mcp_bridge import MCPBridge  # noqa: E402


GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "pressflow-hackathon")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

SERVER_PATH = REPO_ROOT / "tools" / "registry_server.py"

CUSTOMER_MESSAGE = (
    "Hi, I need to know if you guys carry something like 80# matte cover "
    "stock for a postcard run. The customer wants something that feels "
    "heavy and premium but not glossy. What do you have?"
)

SYSTEM_INSTRUCTION = (
    "You are an estimating assistant at a print shop. When a customer "
    "asks about a paper or stock, look it up in the shop's actual stock "
    "registry instead of guessing. If the registry returns ambiguous "
    "matches or fuzzy substring matches, surface them as options to the "
    "customer rather than picking one silently. Keep your final reply "
    "short and operator-friendly."
)


# ----- Tool declarations ----------------------------------------------------
#
# These descriptions mirror the FunctionDeclaration descriptions emitted by
# the MCP server. Gemini reads these to decide *when* and *how* to call the
# tools — the bridge POC proved that's the right surface for tool-routing
# decisions, so we keep the guidance here (not in the system instruction).

QUERY_STOCK_DECL = FunctionDeclaration(
    name="query_stock_registry",
    description=(
        "Search the print shop's actual stock inventory. Use this instead of "
        "guessing what stocks exist — every record returned has a real "
        "PrintIQ code that the parser will accept downstream.\n\n"
        "WHEN TO CALL: any time a customer mentions a paper, stock, or "
        "substrate — fuzzy ('something like 80# matte cover', 'the heavy "
        "gloss stock') or precise ('100# Gloss Cover', '16pt C2S'). Pass "
        "the customer's language verbatim through `text_search`; do not "
        "parse fuzzy language yourself.\n\n"
        "CRITERIA:\n"
        "  • text_search (str) — Free text. Resolves names, aliases, "
        "substring matches.\n"
        "  • basis_weight_min (number) — Lower bound on basis weight "
        "(e.g. 80 for 80lb cover).\n"
        "  • basis_weight_max (number) — Upper bound on basis weight.\n"
        "  • coating ('coated' | 'uncoated' | 'any') — Filter by coating.\n"
        "  • finish (str) — e.g. 'gloss', 'matte', 'silk'.\n\n"
        "Combine `text_search` with structural filters only when the "
        "customer specifies multiple constraints.\n\n"
        "MATCH TIER (each result includes match_tier):\n"
        "  • 'exact' / 'alias'      — confident match. Use directly.\n"
        "  • 'ambiguous'            — multiple candidates; ASK the "
        "customer to disambiguate before committing.\n"
        "  • 'name-substring' / 'alias-substring' / 'token-overlap' — "
        "fuzzy fall-throughs; offer them as options.\n\n"
        "LIMIT (default 3): use 1 when confident; 3-5 when offering "
        "options.\n\n"
        "Returns: {ok: true, kind: 'stock', count, results: [...]} on "
        "success, or {ok: false, error_class, message, query_index, "
        "exit_code} on failure."
    ),
    parameters={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "description": (
                    "Search criteria. Any of: text_search (str), "
                    "basis_weight_min (number), basis_weight_max (number), "
                    "coating ('coated'|'uncoated'|'any'), finish (str)."
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
                "description": "Max results to return (default 3).",
            },
        },
        "required": ["criteria"],
    },
)

QUERY_PRESS_DECL = FunctionDeclaration(
    name="query_press_registry",
    description=(
        "Search the print shop's actual press inventory.\n\n"
        "WHEN TO CALL: a customer or operator mentions a press by name, "
        "alias, or shortname; or you need to know which presses are "
        "available for a given format.\n\n"
        "CRITERIA:\n"
        "  • text_search (str) — Resolves press names, aliases, "
        "shortnames.\n"
        "  • format ('sheet' | 'wide-format' | 'any') — Filter by press "
        "category.\n\n"
        "MATCH TIER (each result includes match_tier when text_search is "
        "used): same semantics as stock — 'exact'/'alias' are confident, "
        "'ambiguous' means ask, fuzzy tiers are suggestions.\n\n"
        "LIMIT (default 3): use 1 for confident lookups; 3-5 to offer "
        "options.\n\n"
        "Returns: {ok: true, kind: 'press', count, results: [...]} on "
        "success, or {ok: false, error_class, message, query_index, "
        "exit_code} on failure."
    ),
    parameters={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "description": (
                    "Search criteria. Any of: text_search (str), "
                    "format ('sheet'|'wide-format'|'any')."
                ),
                "properties": {
                    "text_search": {"type": "string"},
                    "format": {"type": "string"},
                },
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 3).",
            },
        },
        "required": ["criteria"],
    },
)


def _banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def _short_record(rec: dict) -> str:
    """Compact one-line summary of a stock or press record for logs."""
    if "code" in rec:  # stock
        coated = "coated" if rec.get("coated") else "uncoated"
        return (
            f"{rec.get('code')!r}  {rec.get('description', '')}  "
            f"[{rec.get('weight')} {rec.get('weight_unit')}, {coated}, "
            f"tier={rec.get('match_tier', '-')}]"
        )
    # press
    return (
        f"{rec.get('name')!r}  family={rec.get('family')}  "
        f"tier={rec.get('match_tier', '-')}"
    )


def main() -> int:
    _banner("Voomie Registry POC — Vertex AI Gemini ↔ MCP stdio ↔ Racket")

    print(
        f"\n[poc] Initializing Vertex AI "
        f"(project={GCP_PROJECT_ID}, region={GCP_REGION})…"
    )
    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    print("[poc] Vertex initialized.")

    model = GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=[SYSTEM_INSTRUCTION],
        tools=[Tool(function_declarations=[QUERY_STOCK_DECL, QUERY_PRESS_DECL])],
    )

    print(f"\n[poc] Spawning MCP server: {sys.executable} {SERVER_PATH}")

    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
    ) as bridge:
        tools_listed = bridge.list_tools()
        print(f"[poc] MCP session initialized. Tools advertised: {tools_listed}")

        chat = model.start_chat()

        _banner("Sending customer message to Gemini")
        print(CUSTOMER_MESSAGE)
        response = chat.send_message(CUSTOMER_MESSAGE)

        turn = 0
        # Cap at 6 turns to bound runaway tool loops without truncating a
        # reasonable multi-call deliberation.
        MAX_TURNS = 6
        while (
            turn < MAX_TURNS
            and response.candidates
            and response.candidates[0].function_calls
        ):
            turn += 1
            fc = response.candidates[0].function_calls[0]
            args = dict(fc.args) if fc.args else {}

            _banner(f"Turn {turn}: Gemini called {fc.name}")
            print(f"Arguments: {args}")

            if fc.name not in ("query_stock_registry", "query_press_registry"):
                tool_resp: dict = {
                    "ok": False,
                    "error_class": "unknown-tool",
                    "message": f"Unknown tool: {fc.name}",
                }
            else:
                bridge_result = bridge.call_tool(fc.name, args)
                print(
                    f"\n[poc] MCP server returned "
                    f"(is_error={bridge_result['is_error']})."
                )
                tool_resp = bridge_result["structured"] or {
                    "raw": bridge_result["text"]
                }
                # Compact summary so the log stays readable.
                if isinstance(tool_resp, dict) and tool_resp.get("ok"):
                    print(
                        f"  ok=True kind={tool_resp.get('kind')!r} "
                        f"count={tool_resp.get('count')}"
                    )
                    for rec in tool_resp.get("results", []):
                        print(f"    - {_short_record(rec)}")
                elif isinstance(tool_resp, dict):
                    print(
                        f"  ok=False error_class={tool_resp.get('error_class')!r} "
                        f"message={tool_resp.get('message')!r}"
                    )

            response = chat.send_message(
                Part.from_function_response(name=fc.name, response=tool_resp)
            )

        _banner(f"Gemini final response (after {turn} tool turn(s))")
        try:
            print(response.text)
        except Exception as e:
            print(f"(no text content in final response: {e})")

    print("\n[poc] MCP server cleanly terminated. Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[poc] Interrupted by user.")
        sys.exit(130)
