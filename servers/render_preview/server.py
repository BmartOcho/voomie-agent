"""
servers/render_preview/server.py — FastMCP standalone server for the
render_preview tool. Composes FastMCP boilerplate around the pure tool
function in servers/render_preview/tools.py.

Stdio: JSON-RPC framed by MCP on stdin/stdout. Diagnostic logs go to
stderr — same convention as the other Voomie MCP servers.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from servers.render_preview.tools import SHOPTALK_REPO_PATH, render_preview


# Self-announce identity to stderr before MCP takes over stdin/stdout.
print(
    f"[render_preview_server] PID={os.getpid()} "
    f"SHOPTALK_REPO_PATH={SHOPTALK_REPO_PATH} "
    f"PYTHON={sys.executable!r}",
    file=sys.stderr,
    flush=True,
)


mcp = FastMCP("render_preview_server")
mcp.tool()(render_preview)


if __name__ == "__main__":
    mcp.run()
