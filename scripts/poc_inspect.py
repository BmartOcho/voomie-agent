"""
poc_inspect.py — End-to-end proof: Vertex AI Gemini ↔ MCP stdio ↔ PyMuPDF.

Hardcoded test: a customer message saying "I've attached the file for my
postcard job," paired with a real PDF from shoptalk/examples/. Gemini
calls inspect_pdf, reads the structured prepress metadata, and produces a
plain-English summary the CSR could send back to the customer.

The script:
  1. Initializes Vertex AI (Gemini 2.5 Flash) with one tool: inspect_pdf.
  2. Spawns the inspect_pdf MCP server as a subprocess (stdio JSON-RPC).
  3. Sends the customer message + file path to Gemini.
  4. Dispatches Gemini's inspect_pdf call through the MCP bridge.
  5. Routes the inspection response back to Gemini.
  6. Prints the final plain-English summary — which should call out trim
     size, color space, embedded fonts, and any missing/thin bleed.

This is the third proof point: the bridge generalizes to a third tool
class (file inspection, no Racket involved) and FunctionDeclaration
descriptions still drive Gemini's tool-routing decisions.

Env vars (all optional, sensible defaults applied):
  GCP_PROJECT_ID       Vertex AI project   (default: pressflow-hackathon)
  GCP_REGION           Vertex AI region    (default: us-central1)
  POC_PDF_PATH         Override which PDF to inspect. Defaults to the
                       first .pdf found in ~/Desktop/shoptalk/examples/.

Run:
  python scripts/poc_inspect.py
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

SERVER_PATH = REPO_ROOT / "tools" / "inspect_pdf_server.py"
EXAMPLES_DIR = Path.home() / "Desktop" / "shoptalk" / "examples"


def _pick_pdf() -> Path:
    """Resolve the PDF to inspect.

    Honors POC_PDF_PATH if set; otherwise picks the first .pdf in
    ~/Desktop/shoptalk/examples/ (alphabetical). Bails with a clear error
    if no PDF is reachable — the POC is meaningless without real input.
    """
    override = os.environ.get("POC_PDF_PATH")
    if override:
        p = Path(override).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"[poc] POC_PDF_PATH={p} does not exist.")
        return p
    if not EXAMPLES_DIR.exists():
        raise SystemExit(
            f"[poc] {EXAMPLES_DIR} does not exist. "
            "Set POC_PDF_PATH or check that the shoptalk repo is cloned."
        )
    candidates = sorted(EXAMPLES_DIR.glob("*.pdf"))
    if not candidates:
        raise SystemExit(f"[poc] No .pdf files found in {EXAMPLES_DIR}.")
    return candidates[0]


# Customer message + file path stitched into one prompt — mirrors how the
# real Voomie agent will see attachments (path passed alongside the body).
def _customer_message(pdf_path: Path) -> str:
    return (
        "Hi — I've attached the file for my postcard job. "
        "Can you confirm everything looks right for printing? "
        "We need 1000 4x6 postcards, full color, due in two weeks.\n\n"
        f"Attachment (local path): {pdf_path}"
    )


SYSTEM_INSTRUCTION = (
    "You are a prepress assistant at a commercial printer. A customer has "
    "sent a message with a PDF attachment. Call inspect_pdf with the local "
    "file path to read its prepress metadata. Then produce a short, plain-"
    "English summary for the CSR: trim size in inches, color space (call out "
    "RGB if present — that's a problem for offset printing), bleed presence "
    "and adequacy, font embedding, and any warnings the inspection surfaced. "
    "If something will require asking the customer (e.g., missing bleed, RGB "
    "images, unembedded fonts), phrase a short clarifying question they could "
    "send. Be concrete, not generic — quote the actual numbers from the "
    "inspection result."
)


INSPECT_PDF_DECL = FunctionDeclaration(
    name="inspect_pdf",
    description=(
        "Inspect a PDF file at a local filesystem path and return structured "
        "prepress metadata. Use this whenever a customer message references "
        "an attached PDF (or a local path to one). The tool reads:\n"
        "  • page_count, trim_size (inches and points)\n"
        "  • bleed: present, per-side gap in inches, adequate (≥0.125in)\n"
        "  • color_space: dominant (RGB|CMYK|Grayscale), per-kind presence "
        "    flags, mixed indicator\n"
        "  • fonts: all_embedded, embedded_count, unembedded_names\n"
        "  • file_health: encrypted, corrupted, pdf_version\n"
        "  • warnings: human-readable strings for non-fatal issues "
        "    (missing TrimBox, mixed colorspaces, thin bleed, unembedded fonts)\n\n"
        "Returns {ok: false, error_class: encrypted|corrupted|not_found|"
        "not_pdf|internal_error, message: <str>} for unreadable files. "
        "Non-PDF attachments (.jpg, .png, .ai, .indd) yield error_class="
        "'not_pdf' — when you see that, ask the customer to send a PDF."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Absolute path to the PDF file on the local filesystem. "
                    "Use the path the customer message provided — do not "
                    "invent or guess paths."
                ),
            },
        },
        "required": ["file_path"],
    },
)


def _banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def main() -> int:
    pdf_path = _pick_pdf()
    customer_message = _customer_message(pdf_path)

    _banner("Voomie inspect_pdf POC — Vertex AI Gemini ↔ MCP stdio ↔ PyMuPDF")
    print(f"\n[poc] Using PDF: {pdf_path}")

    print(f"\n[poc] Initializing Vertex AI (project={GCP_PROJECT_ID}, region={GCP_REGION})…")
    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    print("[poc] Vertex initialized.")

    model = GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=[SYSTEM_INSTRUCTION],
        tools=[Tool(function_declarations=[INSPECT_PDF_DECL])],
    )

    print(f"\n[poc] Spawning MCP server: {sys.executable} {SERVER_PATH}")

    captured_inspection: dict | None = None

    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
    ) as bridge:
        tools_listed = bridge.list_tools()
        print(f"[poc] MCP session initialized. Tools advertised: {tools_listed}")

        chat = model.start_chat()

        _banner("Sending customer message to Gemini")
        print(customer_message)
        response = chat.send_message(customer_message)

        turn = 0
        while response.candidates and response.candidates[0].function_calls:
            turn += 1
            fc = response.candidates[0].function_calls[0]
            args = dict(fc.args) if fc.args else {}

            _banner(f"Turn {turn}: Gemini called {fc.name}")
            print(f"Arguments: {args}")

            if fc.name != "inspect_pdf":
                tool_resp: dict = {"error": f"Unknown tool: {fc.name}"}
            else:
                bridge_result = bridge.call_tool("inspect_pdf", args)
                print(
                    f"\n[poc] MCP server returned (is_error={bridge_result['is_error']})."
                )
                tool_resp = bridge_result["structured"] or {"raw": bridge_result["text"]}
                # Pretty-print the structured result so the operator can see
                # exactly what Gemini will reason over.
                import json
                print("Structured response:")
                print("-" * 72)
                print(json.dumps(tool_resp, indent=2))
                print("-" * 72)

                if isinstance(tool_resp, dict) and tool_resp.get("ok"):
                    captured_inspection = tool_resp

            response = chat.send_message(
                Part.from_function_response(name=fc.name, response=tool_resp)
            )

        _banner("Gemini final response (plain-English summary)")
        try:
            print(response.text)
        except Exception as e:
            print(f"(no text content in final response: {e})")

        if captured_inspection is None:
            _banner("No successful inspection captured")
            print("inspect_pdf either failed every attempt or was not called.")

    print("\n[poc] MCP server cleanly terminated. Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[poc] Interrupted by user.")
        sys.exit(130)
