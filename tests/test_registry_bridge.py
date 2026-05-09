"""
Test harness for the registry MCP bridge (query_stock_registry,
query_press_registry).

Each test spawns its own MCP server subprocess via lib.mcp_bridge.MCPBridge,
makes real Racket invocations against shoptalk's tools/query-registry.rkt,
and tears the bridge down on teardown. No mocks. The Racket compilation
cache is shared across runs, so per-test cold-start cost is small.

Mirrors the structure of test_parse_shoptalk_bridge.py (same fixture
pattern, same assertion style).

Run:  pytest tests/test_registry_bridge.py -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

REGISTRY_SERVER = REPO_ROOT / "tools" / "registry_server.py"
PARSE_SERVER = REPO_ROOT / "tools" / "parse_shoptalk_server.py"


@pytest.fixture
def bridge():
    """Per-test bridge: spawn the registry MCP server, yield, tear it down."""
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(REGISTRY_SERVER)],
    ) as b:
        yield b


def _structured(result: dict) -> dict:
    """Helper: extract the structured response from a bridge call_tool result."""
    structured = result["structured"]
    assert structured is not None, (
        f"No structured response from MCP server. Raw text was: {result['text']!r}"
    )
    assert isinstance(structured, dict), (
        f"Expected dict from MCP, got {type(structured).__name__}: {structured!r}"
    )
    return structured


def query_stock(bridge: MCPBridge, criteria: dict, limit: int = 3) -> dict:
    return _structured(bridge.call_tool(
        "query_stock_registry", {"criteria": criteria, "limit": limit}
    ))


def query_press(bridge: MCPBridge, criteria: dict, limit: int = 3) -> dict:
    return _structured(bridge.call_tool(
        "query_press_registry", {"criteria": criteria, "limit": limit}
    ))


# ---------------------------------------------------------------------------
# Sanity: the server advertises both tools.
# ---------------------------------------------------------------------------


def test_server_advertises_both_tools(bridge):
    tools = set(bridge.list_tools())
    assert "query_stock_registry" in tools
    assert "query_press_registry" in tools


# ---------------------------------------------------------------------------
# 1. Stock query with no criteria returns up to limit results.
# ---------------------------------------------------------------------------


def test_stock_no_criteria_returns_results(bridge):
    result = query_stock(bridge, {}, limit=3)
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["kind"] == "stock"
    assert result["count"] > 0
    assert result["count"] <= 3
    assert isinstance(result["results"], list)
    assert len(result["results"]) == result["count"]
    # Each record must have at minimum a code and description.
    for rec in result["results"]:
        assert "code" in rec
        assert "description" in rec


# ---------------------------------------------------------------------------
# 2. Structural criteria filter: basis_weight_min + coating.
# ---------------------------------------------------------------------------


def test_stock_structural_criteria_filters_correctly(bridge):
    result = query_stock(
        bridge, {"basis_weight_min": 80, "coating": "coated"}, limit=10
    )
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["kind"] == "stock"
    assert result["count"] >= 1, "expected at least one coated >=80lb stock"
    # Spot-check every returned record obeys the filter.
    for rec in result["results"]:
        assert rec["coated"] is True, (
            f"non-coated stock leaked through coating filter: {rec['code']}"
        )
        assert rec["weight"] >= 80, (
            f"under-weight stock leaked through basis_weight_min: "
            f"{rec['code']} weight={rec['weight']}"
        )


# ---------------------------------------------------------------------------
# 3. Parity test — text_search resolves the canonical record, and the
# resolved PrintIQ code matches what parse_shoptalk emits in its action
# plan for the same alias. This pins agreement between the two MCP
# servers (bridge-side parity check).
# ---------------------------------------------------------------------------


def test_stock_text_search_parity_with_parse_shoptalk(bridge):
    # First: registry resolution.
    registry_result = query_stock(
        bridge, {"text_search": "100 gloss cover"}, limit=3
    )
    assert registry_result["ok"] is True, f"Registry call failed: {registry_result}"
    assert registry_result["count"] >= 1, (
        f"100-gloss-cover should resolve; got count={registry_result['count']}"
    )
    top = registry_result["results"][0]
    assert top["match_tier"] in {"exact", "alias"}, (
        f"top result should be a confident match; got tier={top['match_tier']!r}"
    )
    registry_code = top["code"]
    assert registry_code, "registry result missing 'code'"

    # Second: parse_shoptalk resolution of the same alias. Use a parallel
    # bridge to the parse server.
    postcard = (
        "#lang shoptalk\n"
        'job "Parity Test" {\n'
        "  type:         postcard\n"
        "  finish-size:  5in × 3.5in\n"
        "  stock:        100-gloss-cover\n"
        "}\n"
    )
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(PARSE_SERVER)],
    ) as parse_bridge:
        parse_struct = _structured(
            parse_bridge.call_tool("parse_shoptalk", {"source": postcard})
        )
        assert parse_struct["ok"] is True, f"parse failed: {parse_struct}"
        action_plan = parse_struct["action_plan"]

    # The PrintIQ code returned by the registry must appear in the action
    # plan emitted by parse_shoptalk for the same alias.
    assert registry_code in action_plan, (
        f"Parity violation: registry resolved 100-gloss-cover to code "
        f"{registry_code!r}, but that code does not appear in the "
        f"parse_shoptalk action plan:\n{action_plan}"
    )


# ---------------------------------------------------------------------------
# 4. Press format filter returns only sheet-fed presses.
# ---------------------------------------------------------------------------


def test_press_format_sheet_filter(bridge):
    result = query_press(bridge, {"format": "sheet"}, limit=10)
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["kind"] == "press"
    assert result["count"] >= 1
    SHEET_FAMILIES = {"digital-sheetfed", "offset-sheetfed", "envelope"}
    for rec in result["results"]:
        assert rec["family"] in SHEET_FAMILIES, (
            f"non-sheet press leaked through format='sheet': "
            f"{rec['name']!r} family={rec['family']!r}"
        )


# ---------------------------------------------------------------------------
# 5. Empty result set is a clean success, not an error.
# ---------------------------------------------------------------------------


def test_empty_result_set_returns_ok_count_zero(bridge):
    result = query_stock(bridge, {"basis_weight_min": 9999}, limit=3)
    assert result["ok"] is True, (
        f"Empty result should be ok, not error. Got: {result}"
    )
    assert result["kind"] == "stock"
    assert result["count"] == 0
    assert result["results"] == []


# ---------------------------------------------------------------------------
# 6. Unknown criteria key is a structured criteria-error.
# ---------------------------------------------------------------------------


def test_unknown_criterion_returns_criteria_error(bridge):
    result = query_stock(bridge, {"weight_in_kg": 50}, limit=3)
    assert result["ok"] is False, f"Expected error, got: {result}"
    assert result["error_class"] == "criteria-error"
    assert "weight_in_kg" in result["message"]
    assert result["query_index"] == 0
    assert result["exit_code"] != 0


# ---------------------------------------------------------------------------
# 7. Criterion not valid for kind — passing a stock criterion to press
# returns a structured criteria-error.
# ---------------------------------------------------------------------------


def test_press_with_stock_criterion_returns_criteria_error(bridge):
    result = query_press(bridge, {"coating": "coated"}, limit=3)
    assert result["ok"] is False, f"Expected error, got: {result}"
    assert result["error_class"] == "criteria-error"
    assert "coating" in result["message"]
    assert "press" in result["message"]
    assert result["query_index"] == 0


# ---------------------------------------------------------------------------
# 8. Limit semantics: tier-1 hits don't get topped up from lower tiers.
# Fixture invariant (from shoptalk's own test): "60-uncoated-text-white"
# resolves uniquely at tier-1 (alias hit, code 188160I) AND is a substring
# of "intercon-60-uncoated-text-white" — so a buggy implementation that
# pads tier-1 results with substring fall-throughs would visibly inflate
# the result count above 1.
# ---------------------------------------------------------------------------


def test_limit_one_returns_single_tier1_hit(bridge):
    result = query_stock(
        bridge, {"text_search": "60-uncoated-text-white"}, limit=1
    )
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["count"] == 1
    assert result["results"][0]["match_tier"] in {"exact", "alias"}


def test_limit_high_does_not_top_up_from_lower_tiers(bridge):
    result = query_stock(
        bridge, {"text_search": "60-uncoated-text-white"}, limit=5
    )
    assert result["ok"] is True, f"Expected ok, got: {result}"
    # Even with limit=5, a tier-1 alias hit must not be padded out with
    # substring/token-overlap candidates.
    assert result["count"] == 1, (
        f"tier-1 alias hit must not top up from lower tiers; got count="
        f"{result['count']} with tiers="
        f"{[r.get('match_tier') for r in result['results']]}"
    )
    assert result["results"][0]["match_tier"] == "alias"
    assert result["results"][0]["code"] == "188160I"


# ---------------------------------------------------------------------------
# 9. Ambiguous text_search — all results share match_tier="ambiguous".
# Fixture from shoptalk's own ambiguity test: "100-gloss-text" matches
# multiple records in the alias index.
# ---------------------------------------------------------------------------


def test_ambiguous_text_search_tags_all_results(bridge):
    result = query_stock(
        bridge, {"text_search": "100-gloss-text"}, limit=10
    )
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["count"] >= 2, (
        "100-gloss-text is a known ambiguous alias; expected >=2 hits"
    )
    tiers = {r["match_tier"] for r in result["results"]}
    assert tiers == {"ambiguous"}, (
        f"ambiguous tier must not fall through to substring matches; "
        f"saw tiers={tiers}"
    )


# ---------------------------------------------------------------------------
# 10. Subprocess cleanup — five sequential calls leave no Racket processes
# running. (Python's subprocess.run already reaps children, so the real
# concern is that the server doesn't accidentally spawn detached
# background workers.)
# ---------------------------------------------------------------------------


def _count_racket_processes() -> int:
    """Count currently-running 'racket' processes for this user. Returns 0
    if pgrep is unavailable (the test silently passes that branch)."""
    if shutil.which("pgrep") is None:
        return 0
    proc = subprocess.run(
        ["pgrep", "-x", "racket"], capture_output=True, text=True
    )
    if proc.returncode not in (0, 1):
        return 0  # pgrep unhappy for some reason; don't fail the test on that
    pids = [line for line in proc.stdout.splitlines() if line.strip()]
    return len(pids)


def test_no_racket_zombies_after_repeated_calls(bridge):
    baseline = _count_racket_processes()
    # Five sequential queries through the bridge.
    for _ in range(5):
        result = query_stock(bridge, {"text_search": "cover"}, limit=2)
        assert result["ok"] is True, f"Mid-loop call failed: {result}"
    # Give the kernel a beat to reap; subprocess.run is synchronous so this
    # should already be true, but allow a touch of slack.
    final = _count_racket_processes()
    assert final <= baseline, (
        f"Racket process leak suspected: baseline={baseline} after_5_calls={final}"
    )
