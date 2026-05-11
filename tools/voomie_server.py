"""
tools/voomie_server.py — combined FastMCP server exposing all 11 Voomie
tools from a single MCP endpoint. This is the surface the Voomie agent
loop connects to; it composes the pure tool functions from the five
domain modules under servers/ without modifying any of them.

Stdio: JSON-RPC framed by MCP on stdin/stdout. Diagnostic logs go to
stderr — same convention as the per-domain servers (parse_shoptalk_server,
registry_server, mongodb_server, inspect_pdf_server, render_preview_server).

The sys.path tweak mirrors the existing back-compat shims (see
parse_shoptalk_server.py): when invoked as `python tools/voomie_server.py`,
Python puts tools/ first on sys.path, which would hide the top-level
`servers/` package — prepending the repo root keeps `servers.*` imports
resolvable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from servers.inspect_pdf.tools import inspect_pdf  # noqa: E402
from servers.mongodb.tools import (  # noqa: E402
    append_conversation_turn,
    create_customer,
    flag_for_human,
    lookup_customer,
    persist_job,
    update_job_status,
)
from servers.parse_shoptalk.tools import parse_shoptalk  # noqa: E402
from servers.registry.tools import (  # noqa: E402
    query_press_registry,
    query_stock_registry,
)
from servers.render_preview.tools import render_preview  # noqa: E402


_TOOLS = [
    parse_shoptalk,
    query_stock_registry,
    query_press_registry,
    lookup_customer,
    create_customer,
    update_job_status,
    append_conversation_turn,
    persist_job,
    flag_for_human,
    inspect_pdf,
    render_preview,
]


# Self-announce identity to stderr before MCP takes over stdin/stdout.
# The bridge inherits stderr by default, so this prints to the operator's
# terminal — matching the per-domain server convention.
print(
    f"[voomie_server] PID={os.getpid()} voomie combined server "
    f"tools={len(_TOOLS)}",
    file=sys.stderr,
    flush=True,
)


mcp = FastMCP("voomie")
for _fn in _TOOLS:
    mcp.tool()(_fn)


if __name__ == "__main__":
    mcp.run()
