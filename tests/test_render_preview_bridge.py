"""
Test harness for the render_preview MCP bridge.

Each test spawns its own MCP server subprocess via lib.mcp_bridge.MCPBridge
and makes a real verifier subprocess call. No mocks, no canned PDFs —
every test starts from a real shoptalk source string, runs it through
parse_shoptalk to produce a real action plan, then feeds that plan to
render_preview. This grounds the suite in a true round-trip.

Mirrors test_parse_shoptalk_bridge.py / test_registry_bridge.py /
test_inspect_pdf_bridge.py / test_mongodb_bridge.py — same fixture
pattern, same assertion style.

Run:  pytest tests/test_render_preview_bridge.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

RENDER_SERVER = REPO_ROOT / "tools" / "render_preview_server.py"


# Booklet source — same shape as the long-booklet round-trip case in
# test_parse_shoptalk_bridge.py L151-172, with `ink: 4/4` because booklets
# require an explicit ink declaration (per the parser's §5.6 check).
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def booklet_action_plan() -> str:
    """Real action plan produced by parse_shoptalk on BOOKLET_SRC.

    Module-scoped: parser cold-start is the slow part of this suite, so
    we run it once and reuse. Importing parse_shoptalk directly (not via
    bridge) keeps the test harness from spawning two MCP subprocesses for
    every render test.
    """
    from tools.parse_shoptalk_server import parse_shoptalk
    parsed = parse_shoptalk(BOOKLET_SRC)
    assert parsed.get("ok"), f"Booklet source failed to parse — fix the fixture: {parsed}"
    plan = parsed["action_plan"]
    assert plan.startswith("(job"), f"Action plan doesn't look right: {plan[:80]!r}"
    return plan


@pytest.fixture
def bridge():
    """Per-test bridge: spawn the MCP server, yield, tear it down."""
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(RENDER_SERVER)],
    ) as b:
        yield b


@pytest.fixture
def cleanup_paths():
    """Track output PDFs produced by tests; delete them on teardown.

    Each test appends its output_path to this list, so on success or
    failure the file gets removed and /tmp doesn't accumulate PDFs.
    """
    paths: list[Path] = []
    yield paths
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            pass


def _structured(result: dict) -> dict:
    structured = result["structured"]
    assert structured is not None, (
        f"No structured response from MCP server. Raw text was: {result['text']!r}"
    )
    assert isinstance(structured, dict), (
        f"Expected dict from MCP, got {type(structured).__name__}: {structured!r}"
    )
    return structured


def _render(bridge: MCPBridge, action_plan: str, output_path: str = "") -> dict:
    return _structured(bridge.call_tool(
        "render_preview",
        {"action_plan": action_plan, "output_path": output_path},
    ))


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_server_advertises_render_preview_tool(bridge):
    tools = set(bridge.list_tools())
    assert "render_preview" in tools, f"Expected render_preview in {tools}"


# ---------------------------------------------------------------------------
# Success-path tests
# ---------------------------------------------------------------------------


def test_valid_booklet_action_plan_renders(bridge, booklet_action_plan, cleanup_paths):
    result = _render(bridge, booklet_action_plan)
    assert result["ok"] is True, result
    cleanup_paths.append(result["output_path"])
    assert Path(result["output_path"]).exists()
    assert result["file_size_bytes"] > 0


def test_output_pdf_is_a_real_pdf(bridge, booklet_action_plan, cleanup_paths):
    """First 4 bytes must be the PDF magic number %PDF — otherwise the
    verifier wrote something we can't ship downstream."""
    result = _render(bridge, booklet_action_plan)
    assert result["ok"] is True, result
    cleanup_paths.append(result["output_path"])

    with open(result["output_path"], "rb") as f:
        magic = f.read(4)
    assert magic == b"%PDF", f"Expected %PDF magic, got {magic!r}"


def test_custom_output_path_is_respected(bridge, booklet_action_plan, tmp_path, cleanup_paths):
    target = tmp_path / "subdir" / "custom-name.pdf"
    # Parent dir doesn't exist — the tool should create it.
    assert not target.parent.exists()

    result = _render(bridge, booklet_action_plan, output_path=str(target))
    assert result["ok"] is True, result
    cleanup_paths.append(result["output_path"])

    assert result["output_path"] == str(target)
    assert target.exists()
    assert target.parent.exists()


def test_empty_output_path_uses_temp_path_with_pdf_suffix(
    bridge, booklet_action_plan, cleanup_paths
):
    result = _render(bridge, booklet_action_plan, output_path="")
    assert result["ok"] is True, result
    cleanup_paths.append(result["output_path"])

    # Default placement should be the OS temp dir and the file should be
    # a .pdf so downstream consumers (Preview.app, browser) recognize it.
    out = Path(result["output_path"])
    assert out.suffix == ".pdf", f"Expected .pdf suffix, got {out!r}"
    # On macOS the OS temp dir lives under /var/folders or /tmp depending
    # on TMPDIR — either is fine, but it should NOT be inside the repo.
    assert REPO_ROOT not in out.parents, (
        f"Default output landed inside the repo: {out}"
    )


def test_input_temp_file_is_cleaned_up_after_render(
    bridge, booklet_action_plan, cleanup_paths
):
    """The tool writes the action plan to a temp .txt before invoking the
    verifier. After the call returns (success OR failure), that input
    file must be gone — accumulating temp files would leak into the
    runner's filesystem over a long demo session."""
    import glob, tempfile

    tmp_dir = tempfile.gettempdir()
    pre = set(glob.glob(os.path.join(tmp_dir, "voomie-plan-*.txt")))

    result = _render(bridge, booklet_action_plan)
    assert result["ok"] is True, result
    cleanup_paths.append(result["output_path"])

    post = set(glob.glob(os.path.join(tmp_dir, "voomie-plan-*.txt")))
    leaked = post - pre
    assert not leaked, (
        f"render_preview left {len(leaked)} input temp file(s) on disk: {leaked}"
    )


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_malformed_action_plan_returns_render_error(bridge):
    """Garbage input — verifier traceback must surface in stderr."""
    result = _render(bridge, "this is not an s-expression at all")
    assert result["ok"] is False, result
    assert result["error_class"] == "render_error"
    assert result["stderr"], "Expected a non-empty stderr from the verifier"


def test_empty_action_plan_returns_error_without_invoking_verifier(bridge):
    """Empty / whitespace-only input is a programmer mistake; reject it
    before the subprocess is even started."""
    result = _render(bridge, "")
    assert result["ok"] is False, result
    assert result["error_class"] == "render_error"
    assert "empty" in result["message"].lower(), result["message"]
