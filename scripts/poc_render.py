"""
poc_render.py — End-to-end booklet preview pipeline:
  shoptalk source → parse_shoptalk → action plan → render_preview → PDF.

This is the demo's closing beat: "here's what the machine-readable
contract enables downstream." A locked saddle-stitch booklet declaration
is parsed by shoptalk's spec parser, the action plan is fed to the
Python verifier, and a real preview PDF is opened on screen.

Two MCP servers are spawned in sequence (one bridge per tool, same as
production usage in the agent loop). No Vertex/Gemini in this POC — the
booklet declaration is pre-baked, so the agent is not in the loop. The
point is to demonstrate that parse and render compose cleanly.

Env vars:
  SHOPTALK_REPO_PATH  Path to shoptalk repo  (required)
  RACKET_BIN          Path to parser binary  (required)
  POC_OUTPUT_PATH     Override the output PDF path. Defaults to a uuid
                      under the OS temp dir. Useful when recording a demo
                      and you want a stable filename to point Preview at.

Run:
  python scripts/poc_render.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

PARSE_SERVER = REPO_ROOT / "tools" / "parse_shoptalk_server.py"
RENDER_SERVER = REPO_ROOT / "tools" / "render_preview_server.py"


# Locked booklet — same fixture used in the test_render_preview suite.
# Includes ink: 4/4 (booklets require explicit ink) and a full set of
# metadata fields so the verifier has something interesting to render.
BOOKLET_SRC = """\
#lang shoptalk
job "Annual Report Booklet" {
  type:          booklet
  binding:       saddle-stitch
  finish-size:   8.5in × 11in
  page-count:    32
  quantity:      750
  text-stock:    "100#GlossTextDigitalSize"
  cover-stock:   830620
  press:         big-fuji
  sheet-size:    13in × 19in
  ink:           4/4
  proof:         pdf-email
  packaging:     bulk-box
  finishing:     [trim, score, fold]
  due:           2026-08-01
  customer:      "Acme Annual Report Co"
  customer-code: "ACME-AR-001"
  po-number:     "PO-99999"
  notes:         "Annual report 2025 — 28 interior pages plus cover"
}
"""


def _banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def _open_pdf(path: str) -> None:
    """Best-effort: open the PDF in the OS default viewer.

    Wrapped in try/except so a non-Mac environment (or a headless CI
    runner) doesn't fail the POC. macOS uses `open`; other platforms
    silently skip — the path is still printed for the operator.
    """
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=True)
        else:
            print(f"[poc] Auto-open skipped on platform={sys.platform!r}.")
    except Exception as e:  # noqa: BLE001
        print(f"[poc] Auto-open failed ({e}); open manually: {path}")


def main() -> int:
    _banner("Voomie render_preview POC — full booklet pipeline")

    # ------------------------------------------------------------------
    # Step 1: Parse the shoptalk source via the parse_shoptalk MCP server.
    # ------------------------------------------------------------------
    _banner("Step 1: parse shoptalk source → action plan")
    print(BOOKLET_SRC)

    print("[poc] Spawning parse_shoptalk MCP server…")
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(PARSE_SERVER)],
    ) as parse_bridge:
        parse_result = parse_bridge.call_tool(
            "parse_shoptalk", {"source": BOOKLET_SRC}
        )
        parse_struct = parse_result["structured"] or {}

    if not parse_struct.get("ok"):
        print("[poc] parse_shoptalk FAILED:")
        print(json.dumps(parse_struct, indent=2))
        return 1

    action_plan = parse_struct["action_plan"]
    print()
    print(f"[poc] Parse OK ({len(action_plan)} chars of action plan).")
    _banner("Action plan (verifier input)")
    print(action_plan)

    # ------------------------------------------------------------------
    # Step 2: Render the action plan via the render_preview MCP server.
    # ------------------------------------------------------------------
    _banner("Step 2: render action plan → preview PDF")
    print("[poc] Spawning render_preview MCP server…")

    import os
    output_override = os.environ.get("POC_OUTPUT_PATH", "")

    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(RENDER_SERVER)],
    ) as render_bridge:
        render_result = render_bridge.call_tool(
            "render_preview",
            {"action_plan": action_plan, "output_path": output_override},
        )
        render_struct = render_result["structured"] or {}

    if not render_struct.get("ok"):
        print("[poc] render_preview FAILED:")
        print(json.dumps(render_struct, indent=2))
        return 1

    output_path = render_struct["output_path"]
    file_size = render_struct["file_size_bytes"]

    _banner("Render result")
    print(f"  output_path:     {output_path}")
    print(f"  file_size_bytes: {file_size:,}")

    # ------------------------------------------------------------------
    # Step 3: Open the PDF on screen (macOS — best-effort elsewhere).
    # ------------------------------------------------------------------
    _banner("Step 3: opening preview")
    print(f"[poc] Opening: {output_path}")
    _open_pdf(output_path)

    print("\n[poc] Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[poc] Interrupted by user.")
        sys.exit(130)
