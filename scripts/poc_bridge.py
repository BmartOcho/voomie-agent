"""
poc_bridge.py — End-to-end proof: Vertex AI Gemini ↔ MCP stdio ↔ Racket.

Hardcoded test: customer message #3 from SPEC.md (a 4 x 9 candidate push card).

The script:
  1. Initializes Vertex AI (Gemini 2.5 Flash) with one tool: parse_shoptalk.
  2. Spawns the MCP server as a subprocess (stdio JSON-RPC).
  3. Sends the customer message to Gemini.
  4. Dispatches Gemini's parse_shoptalk function calls through the MCP bridge.
  5. Routes the parser's response back to Gemini.
  6. Prints the final Gemini response and the raw action-plan s-expression
     captured directly from the parser when one is produced.

Env vars (all optional, sensible defaults applied):
  GCP_PROJECT_ID       Vertex AI project       (default: pressflow-hackathon)
  GCP_REGION           Vertex AI region        (default: us-central1)
  SHOPTALK_REPO_PATH   shoptalk repo path      (default: ~/Desktop/shoptalk)
  RACKET_BIN           Racket binary           (default: /Applications/Racket v9.1/bin/racket)

Run:
  python scripts/poc_bridge.py
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

SERVER_PATH = REPO_ROOT / "tools" / "parse_shoptalk_server.py"

CUSTOMER_MESSAGE = """\
Please quote 4 x 9 candidate push cards, 4/4 bleed, 100# cover, C2S
Quantity: 150

Thank you,
Cindy Meyer
"""

SYSTEM_INSTRUCTION = (
    "You are translating a customer print-job inquiry into a shoptalk declaration. "
    "Call parse_shoptalk with a candidate declaration. If it parses, report success "
    "and show the action plan. If it errors, report the error."
)

PARSE_SHOPTALK_DECL = FunctionDeclaration(
    name="parse_shoptalk",
    description=(
        "Parse a #lang shoptalk source program with the shoptalk Racket parser. "
        "shoptalk's grammar is NOT Racket s-expressions; it uses a "
        '`job "<name>" { field: value }` block form. Minimal valid postcard:\n\n'
        "#lang shoptalk\n"
        'job "Sample Postcard" {\n'
        "  type:         postcard\n"
        "  finish-size:  5in × 3.5in\n"
        "  quantity:     500\n"
        "  stock:        100-gloss-cover\n"
        "  press:        big-fuji\n"
        "  due:          2026-06-30\n"
        "}\n\n"
        "Field syntax: `<name>: <value>` with whitespace separation. Fields end at "
        "newline (no commas, no semicolons). Comments are `// …` to end of line.\n\n"
        "Type values include: postcard, business-card, booklet, banner, trifold, "
        "book. The postcard validator enforces USPS bounds (width 3.5in–6in, "
        "height 3.5in–4.25in); oversized cards (e.g. 4×9 push cards) should use a "
        "non-postcard type, in which case dimensional validation is skipped.\n\n"
        "Required for postcards: type, finish-size. Quantity, stock, press, and due "
        "round-trip without parser enforcement (the agent should still emit them). "
        "Ink defaults to 4/4 (full-color both sides) if omitted; valid token shape "
        "is N/N where N ∈ {0,1,2,4}.\n\n"
        "Returns {ok: true, action_plan: <s-expression text>, warnings: <stderr text>} "
        "on a clean parse, or {ok: false, error_class: "
        "lexer|parse|validation|config|timeout|other, message: <stderr body>, "
        "exit_code: <int>} on failure."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Full #lang shoptalk source program, beginning with `#lang shoptalk`.",
            },
        },
        "required": ["source"],
    },
)


def _banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def main() -> int:
    _banner("Voomie Bridge POC — Vertex AI Gemini ↔ MCP stdio ↔ Racket")

    print(f"\n[poc] Initializing Vertex AI (project={GCP_PROJECT_ID}, region={GCP_REGION})…")
    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    print("[poc] Vertex initialized.")

    model = GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=[SYSTEM_INSTRUCTION],
        tools=[Tool(function_declarations=[PARSE_SHOPTALK_DECL])],
    )

    print(f"\n[poc] Spawning MCP server: {sys.executable} {SERVER_PATH}")

    captured_action_plan: str | None = None

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
        while response.candidates and response.candidates[0].function_calls:
            turn += 1
            fc = response.candidates[0].function_calls[0]
            args = dict(fc.args) if fc.args else {}

            _banner(f"Turn {turn}: Gemini called {fc.name}")
            source = args.get("source", "")
            print("Arguments (source):")
            print("-" * 72)
            print(source)
            print("-" * 72)

            if fc.name != "parse_shoptalk":
                tool_resp: dict = {"error": f"Unknown tool: {fc.name}"}
            else:
                bridge_result = bridge.call_tool(
                    "parse_shoptalk", {"source": source}
                )
                print(f"\n[poc] MCP server returned (is_error={bridge_result['is_error']}).")
                print("Raw text content from MCP:")
                print("-" * 72)
                print(bridge_result["text"])
                print("-" * 72)

                tool_resp = bridge_result["structured"] or {"raw": bridge_result["text"]}
                if isinstance(tool_resp, dict) and tool_resp.get("ok"):
                    captured_action_plan = (
                        tool_resp.get("action_plan", "") or captured_action_plan
                    )

            response = chat.send_message(
                Part.from_function_response(name=fc.name, response=tool_resp)
            )

        _banner("Gemini final response")
        try:
            print(response.text)
        except Exception as e:
            print(f"(no text content in final response: {e})")

        if captured_action_plan:
            _banner("Raw action plan from Racket")
            print(captured_action_plan)
        else:
            _banner("No action plan was captured")
            print("parse_shoptalk either failed every attempt or was not called.")

    print("\n[poc] MCP server cleanly terminated. Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[poc] Interrupted by user.")
        sys.exit(130)
