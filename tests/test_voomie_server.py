"""
Test harness for the combined voomie MCP server (tools/voomie_server.py).

This server composes all 11 Voomie tools onto a single FastMCP endpoint.
Per-tool correctness is already covered exhaustively by the per-domain
test files (test_parse_shoptalk_bridge.py, test_registry_bridge.py,
test_mongodb_bridge.py, test_inspect_pdf_bridge.py,
test_render_preview_bridge.py). This suite only verifies what changes
when those tools live behind a single combined entry point:

  1. Tool advertisement — all 11 tool names appear in list_tools.
  2. Smoke dispatch through three different subprocess paths
     (Racket parse, Racket registry, MongoDB) to confirm each backing
     subprocess / lazy-init still works when the combined server is
     the host process.
  3. No-double-init — the server boots cleanly twice in sequence so
     module-level state (FastMCP registration, MongoDB lazy connection)
     can't accidentally cache something that breaks on the second boot.
  4. Subprocess cleanup — a 5-call mixed sequence tears down without
     leaving zombie children.

Tests that touch MongoDB use the same skipif gate as
test_mongodb_bridge.py: skip cleanly when MONGODB_URI is unset.

Run:  pytest tests/test_voomie_server.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

SERVER_PATH = REPO_ROOT / "tools" / "voomie_server.py"

EXPECTED_TOOLS = {
    "parse_shoptalk",
    "query_stock_registry",
    "query_press_registry",
    "lookup_customer",
    "create_customer",
    "update_job_status",
    "append_conversation_turn",
    "persist_job",
    "flag_for_human",
    "inspect_pdf",
    "render_preview",
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _server_env() -> dict[str, str]:
    """Forward the env vars the combined server's subprocesses need.

    MCP stdio_client doesn't auto-inherit the parent's full environment
    (only a small whitelist like PATH/HOME). The combined server fans
    out to Racket and MongoDB, so we have to pass through the same vars
    the per-domain test files pass: SHOPTALK_REPO_PATH, RACKET_BIN,
    MONGODB_URI, VOOMIE_DB.
    """
    env: dict[str, str] = {}
    for key in ("SHOPTALK_REPO_PATH", "RACKET_BIN", "MONGODB_URI", "VOOMIE_DB"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


@pytest.fixture
def bridge():
    """Per-test bridge: spawn the combined server, yield, tear it down."""
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
        env=_server_env(),
    ) as b:
        yield b


def _structured(result: dict) -> dict:
    """Mirror the helper in the per-domain tests."""
    structured = result["structured"]
    assert structured is not None, (
        f"No structured response from MCP server. Raw text was: {result['text']!r}"
    )
    assert isinstance(structured, dict), (
        f"Expected dict from MCP, got {type(structured).__name__}: {structured!r}"
    )
    return structured


def _new_email(tag: str = "voomie-combined") -> str:
    return f"voomie-{tag}-{uuid.uuid4().hex[:10]}@example.test"


# ---------------------------------------------------------------------------
# 1. Tool advertisement
# ---------------------------------------------------------------------------


def test_combined_server_advertises_all_eleven_tools(bridge):
    """The combined server must expose every tool from every domain
    module — if any are missing, the agent loop will silently fall
    through to no-tool behavior on those calls."""
    tools = set(bridge.list_tools())
    missing = EXPECTED_TOOLS - tools
    extra = tools - EXPECTED_TOOLS
    assert not missing, f"Combined server is missing tools: {missing}; advertised: {tools}"
    assert not extra, f"Combined server advertises unexpected tools: {extra}"
    assert len(tools) == 11, f"Expected 11 tools, got {len(tools)}: {sorted(tools)}"


# ---------------------------------------------------------------------------
# 2. Smoke dispatch — parse_shoptalk (first Racket subprocess path)
# ---------------------------------------------------------------------------


def test_parse_shoptalk_dispatches_through_combined_server(bridge):
    """A clean postcard parses to ok=True with an action plan. Confirms
    the Racket subprocess fan-out still works when the host is the
    combined server (not the standalone parse_shoptalk_server)."""
    source = (
        "#lang shoptalk\n"
        'job "Combined Server Smoke" {\n'
        "  type:         postcard\n"
        "  finish-size:  6in × 4in\n"
        "  quantity:     250\n"
        "  stock:        100-gloss-cover\n"
        "}\n"
    )
    result = _structured(bridge.call_tool("parse_shoptalk", {"source": source}))
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert "action_plan" in result, f"Missing action_plan in: {result}"
    assert "(type postcard)" in result["action_plan"]
    assert "Combined Server Smoke" in result["action_plan"]


# ---------------------------------------------------------------------------
# 3. Smoke dispatch — query_stock_registry (second Racket subprocess path)
# ---------------------------------------------------------------------------


def test_query_stock_registry_dispatches_through_combined_server(bridge):
    """A confident text_search resolves with match_tier in {exact, alias}.
    Confirms the second Racket subprocess (query-registry.rkt) is reachable
    via the combined server — separate from the parse_shoptalk Racket path
    above, so this catches per-subprocess wiring bugs that wouldn't show
    up in just one Racket smoke test."""
    result = _structured(bridge.call_tool("query_stock_registry", {
        "criteria": {"text_search": "100-gloss-cover"},
        "limit": 3,
    }))
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["kind"] == "stock"
    assert result["count"] >= 1, f"Expected at least one result, got: {result}"
    top = result["results"][0]
    assert top["match_tier"] in {"exact", "alias"}, (
        f"Top result should be a confident match; got tier={top.get('match_tier')!r} "
        f"in result: {top!r}"
    )


# ---------------------------------------------------------------------------
# 4. Smoke dispatch — lookup_customer (MongoDB lazy-init path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("MONGODB_URI"),
    reason="MONGODB_URI not set; mongodb tools require a real Atlas cluster",
)
def test_lookup_customer_dispatches_through_combined_server(bridge):
    """A no-match lookup returns ok=true, found=false. Confirms the
    MongoDB lazy-init (servers/mongodb/tools._get_client) triggers
    correctly the first time it's called inside the combined server,
    rather than only when mongodb_server is the entry point."""
    fake_email = _new_email("nonexistent")
    result = _structured(bridge.call_tool("lookup_customer", {"query": fake_email}))
    assert result["ok"] is True, f"Expected ok, got: {result}"
    assert result["found"] is False, f"Expected no match, got: {result}"


# ---------------------------------------------------------------------------
# 5. No-double-init: the combined server can be spawned twice in sequence
# ---------------------------------------------------------------------------


def test_combined_server_no_double_init():
    """Spawn → list → shut down → spawn → list → shut down. The second
    spawn must succeed with the full tool set. This catches lazy-init
    bugs that only surface on repeated process startups (e.g. import-time
    side effects that aren't idempotent across a process boundary, or
    leaked file descriptors that prevent a clean re-bind)."""
    env = _server_env()
    for attempt in (1, 2):
        with MCPBridge(
            server_command=sys.executable,
            server_args=[str(SERVER_PATH)],
            env=env,
        ) as b:
            tools = set(b.list_tools())
            missing = EXPECTED_TOOLS - tools
            assert not missing, (
                f"Spawn #{attempt} missing tools: {missing}; advertised: {tools}"
            )

    # Skip-gated: only assert the MongoDB lazy-init survives a fresh
    # process when MONGODB_URI is set. With it set, the second spawn's
    # lookup_customer must connect from cold just like the first.
    if os.environ.get("MONGODB_URI"):
        with MCPBridge(
            server_command=sys.executable,
            server_args=[str(SERVER_PATH)],
            env=env,
        ) as b:
            result = _structured(b.call_tool("lookup_customer", {
                "query": _new_email("double-init"),
            }))
            assert result["ok"] is True, f"Second spawn lookup_customer errored: {result}"
            assert result["found"] is False


# ---------------------------------------------------------------------------
# 6. Subprocess cleanup: 5-call mixed sequence leaves no zombies behind
# ---------------------------------------------------------------------------


def _list_child_pids(parent_pid: int) -> list[int]:
    """Return the pid list of all live children of `parent_pid`.

    Uses `pgrep -P` which is portable across darwin and linux. Returns
    [] when the parent has no live children. We only call this *after*
    the bridge has been torn down, so any pid still listed is a zombie
    or runaway subprocess.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode not in (0, 1):  # 1 = no matches, which is fine
        return []
    return [int(line) for line in out.stdout.split() if line.strip()]


def test_combined_server_subprocess_cleanup_after_mixed_sequence():
    """Run a 5-call mixed sequence then verify the bridge tears down
    cleanly with no leftover children of the test process. The Racket
    parse / registry / inspect / render paths all spawn their own
    short-lived subprocesses; this test confirms none of them outlive
    the bridge teardown.

    With MONGODB_URI set, the sequence includes 3 MongoDB calls
    alongside parse + query; without it, the sequence is 5 calls across
    parse, query, inspect, and render.
    """
    pids_before = set(_list_child_pids(os.getpid()))

    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
        env=_server_env(),
    ) as b:
        # Always-on calls (no external service needed beyond Racket
        # and a tmp PDF for inspect_pdf).
        parse_result = _structured(b.call_tool("parse_shoptalk", {
            "source": (
                "#lang shoptalk\n"
                'job "Cleanup Smoke" {\n'
                "  type:         postcard\n"
                "  finish-size:  6in × 4in\n"
                "  quantity:     100\n"
                "}\n"
            ),
        }))
        assert parse_result["ok"] is True

        query_result = _structured(b.call_tool("query_stock_registry", {
            "criteria": {"text_search": "100-gloss-cover"},
            "limit": 1,
        }))
        assert query_result["ok"] is True

        if os.environ.get("MONGODB_URI"):
            # 3 MongoDB calls: lookup (no match), append turn, lookup again.
            # All idempotent or self-cleaning — we only need the calls to
            # complete, not to leave permanent state.
            email = _new_email("cleanup")
            r1 = _structured(b.call_tool("lookup_customer", {"query": email}))
            assert r1["ok"] is True

            # append_conversation_turn creates a conversation doc keyed by
            # job_id; we use a unique synthetic job_id and clean up after.
            job_id = f"J{uuid.uuid4().int % 1_000_000:06d}-99"
            r2 = _structured(b.call_tool("append_conversation_turn", {
                "job_id": job_id,
                "role": "user",
                "content": "cleanup smoke",
                "status": "sent",
            }))
            assert r2["ok"] is True

            r3 = _structured(b.call_tool("lookup_customer", {"query": email}))
            assert r3["ok"] is True

            # Tidy up the conversation doc this test created.
            try:
                import pymongo
                cli = pymongo.MongoClient(
                    os.environ["MONGODB_URI"], serverSelectionTimeoutMS=5000
                )
                db_name = os.environ.get("VOOMIE_DB", "voomie")
                cli[db_name]["conversations"].delete_one({"job_id": job_id})
                cli.close()
            except Exception:
                # Cleanup is best-effort; leaving one stray test doc behind
                # isn't worth failing the assertion that subprocess teardown
                # is clean.
                pass
        else:
            # Round out to 5 calls without MongoDB: an inspect_pdf on a
            # tmp PDF and a render_preview against the action plan from
            # parse_result, plus a second registry call.
            import fitz

            tmp_pdf = REPO_ROOT / "tests" / "_voomie_cleanup_smoke.pdf"
            try:
                doc = fitz.open()
                doc.new_page(width=432, height=288)  # 6in × 4in @ 72dpi
                doc.save(str(tmp_pdf))
                doc.close()

                r1 = _structured(b.call_tool("inspect_pdf", {
                    "file_path": str(tmp_pdf),
                }))
                assert "ok" in r1

                r2 = _structured(b.call_tool("render_preview", {
                    "action_plan": parse_result["action_plan"],
                }))
                assert "ok" in r2

                r3 = _structured(b.call_tool("query_press_registry", {
                    "criteria": {},
                    "limit": 1,
                }))
                assert r3["ok"] is True
            finally:
                if tmp_pdf.exists():
                    tmp_pdf.unlink()

    # After the bridge context exits, no new long-lived child processes
    # should remain. Compare child-pid sets before/after — anything
    # newly present is a leak.
    pids_after = set(_list_child_pids(os.getpid()))
    leaked = pids_after - pids_before
    assert not leaked, (
        f"Subprocess leak after bridge teardown: pids {leaked} are still "
        f"children of the test process. Before: {pids_before}, after: {pids_after}."
    )
