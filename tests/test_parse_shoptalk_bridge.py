"""
Test harness for the parse_shoptalk MCP bridge.

Each test spawns its own MCP server subprocess via lib.mcp_bridge.MCPBridge,
makes a real Racket invocation against the shoptalk parser, and tears the
bridge down on teardown. No mocks. The Racket compilation cache is shared
across runs, so per-test cold-start cost is small after the first invocation.

Assertions target the *structured shape* of the response (the keys and
their type/category) rather than exact stderr wording, since Racket's
error strings are not a stable contract.

Run:  pytest tests/test_parse_shoptalk_bridge.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

SERVER_PATH = REPO_ROOT / "tools" / "parse_shoptalk_server.py"


@pytest.fixture
def bridge():
    """Per-test bridge: spawn the MCP server, yield, tear it down."""
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
    ) as b:
        yield b


def parse(bridge: MCPBridge, source: str) -> dict:
    """Helper: invoke parse_shoptalk and return the structured response.

    Asserts that the bridge produced a structured response (the shape the
    Vertex agent will see). The structured dict is what the rest of the
    test asserts on.
    """
    result = bridge.call_tool("parse_shoptalk", {"source": source})
    structured = result["structured"]
    assert structured is not None, (
        f"No structured response from MCP server. Raw text was: {result['text']!r}"
    )
    assert isinstance(structured, dict), (
        f"Expected dict from MCP, got {type(structured).__name__}: {structured!r}"
    )
    return structured


# ---------------------------------------------------------------------------
# Success-path tests
# ---------------------------------------------------------------------------


def test_clean_postcard_returns_action_plan(bridge):
    """A USPS-valid postcard (6in × 4in landscape — a "4×6 postcard" in
    print-shop parlance) parses cleanly with type=postcard in the plan."""
    source = """\
#lang shoptalk
job "Test Postcard" {
  type:         postcard
  finish-size:  6in × 4in
  quantity:     500
  stock:        100-gloss-cover
  press:        big-fuji
  due:          2026-06-30
}
"""
    result = parse(bridge, source)
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert "(type postcard)" in result["action_plan"]
    assert "Test Postcard" in result["action_plan"]


def test_clean_flat_card_returns_action_plan(bridge):
    """A 4×9 flat-card. type=flat-card has no validator (per AGENT-NOTES.md
    §A.3 / B-i), so the postcard USPS check is skipped and the parser
    emits an action plan with the type token round-tripped."""
    source = """\
#lang shoptalk
job "Push Card" {
  type:         flat-card
  finish-size:  4in × 9in
  quantity:     150
  stock:        100-gloss-cover
}
"""
    result = parse(bridge, source)
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert "(type flat-card)" in result["action_plan"]


def test_stock_alias_resolves_in_action_plan(bridge):
    """Per AGENT-NOTES.md §B-iii alias resolution: '100-gloss-cover' resolves
    via lookup-stock and the action plan emits the structured form
    (stock <desc> (printiq-code <code>)) with an Info: line in stderr."""
    source = """\
#lang shoptalk
job "Alias Test" {
  type:         postcard
  finish-size:  5in × 3.5in
  stock:        100-gloss-cover
}
"""
    result = parse(bridge, source)
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert "printiq-code" in result["action_plan"]
    assert "100#GlossCoverDigitalSize" in result["action_plan"]
    # Stderr should mention the resolution
    assert "100-gloss-cover" in result["warnings"]
    assert "alias" in result["warnings"].lower() or "resolved" in result["warnings"].lower()


def test_unknown_stock_warns_but_succeeds(bridge):
    """Per AGENT-NOTES.md §B-iii ('miss cands' branch): an unknown stock
    emits a Warning to stderr, the action plan still emits with the bare
    symbol form, and the exit code is 0."""
    source = """\
#lang shoptalk
job "Unknown Stock" {
  type:         postcard
  finish-size:  5in × 3.5in
  stock:        purple-glitter-paper-9000
}
"""
    result = parse(bridge, source)
    assert result["ok"] is True, f"Expected warning + ok exit, got: {result}"
    # The warning should mention either the stock or the registry
    warnings = result["warnings"]
    assert (
        "purple-glitter-paper-9000" in warnings
        or "registry" in warnings.lower()
        or "warning" in warnings.lower()
    ), f"Expected a stock-warning message in stderr, got: {warnings!r}"


def test_long_booklet_round_trips_without_truncation(bridge):
    """A real booklet declaration (cover-stock, text-stock, plus optional
    finishing and metadata fields) round-trips through the bridge intact.
    Confirms the bridge isn't silently truncating large payloads in either
    direction."""
    source = """\
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
    result = parse(bridge, source)
    assert result["ok"] is True, f"Expected ok, got: {result}"
    plan = result["action_plan"]
    # Structural assertions: name, type, both stocks, finishing, metadata
    assert "(type booklet)" in plan
    assert "Annual Report Booklet" in plan
    assert "100#GlossTextDigitalSize" in plan
    assert "830620" in plan or "Cover" in plan  # cover stock resolves to a description
    assert "ACME-AR-001" in plan
    # Sanity: not truncated to a small size
    assert len(plan) > 400, f"Action plan suspiciously short ({len(plan)} chars): {plan!r}"


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_postcard_outside_usps_bounds_errors(bridge):
    """Per AGENT-NOTES.md §B-v: a postcard with finish-size outside USPS
    bounds (3.5–6 width × 3.5–4.25 height) hard-errors via raw `error`
    with exit 1; the message names USPS dimensional rules."""
    source = """\
#lang shoptalk
job "Too Big" {
  type:         postcard
  finish-size:  7in × 5in
  quantity:     100
}
"""
    result = parse(bridge, source)
    assert result["ok"] is False, f"Expected error, got: {result}"
    assert result["exit_code"] == 1
    assert result["error_class"] == "validation", (
        f"Expected validation error class, got: {result['error_class']!r} "
        f"(message: {result['message']!r})"
    )
    assert "usps" in result["message"].lower(), (
        f"Expected 'USPS' in message, got: {result['message']!r}"
    )


def test_malformed_source_returns_structured_parse_error(bridge):
    """Missing colon between field name and value triggers a parse error
    (per AGENT-NOTES.md §D.2 the parse-error message contains
    'Encountered parsing error near …' with line/column info)."""
    source = """\
#lang shoptalk
job "Bad Syntax" {
  type postcard
  finish-size: 5in × 3.5in
}
"""
    result = parse(bridge, source)
    assert result["ok"] is False, f"Expected parse error, got: {result}"
    assert result["exit_code"] == 1
    # The malformation lands either in the lexer or the parser depending
    # on how the tokens line up; both are acceptable parse-failure classes.
    assert result["error_class"] in {"parse", "lexer"}, (
        f"Expected parse|lexer error class, got: {result['error_class']!r}"
    )
    assert result["message"], "Expected non-empty error message"


def test_empty_source_does_not_hang_or_crash(bridge):
    """An empty source string must produce a clean structured error rather
    than hanging the bridge or crashing the server."""
    result = parse(bridge, "")
    assert result["ok"] is False, f"Expected error on empty input, got: {result}"
    assert result["message"], "Expected a non-empty error message for empty source"
    # Exit code may vary (Racket may complain about missing #lang); we just
    # require that the bridge transports a sensible structured response.
    assert "exit_code" in result
