"""
servers/registry/server.py — FastMCP standalone server for the registry
tools (query_stock_registry, query_press_registry). Composes FastMCP
boilerplate around the pure tool functions in servers/registry/tools.py.

Stdio: JSON-RPC framed by MCP on stdin/stdout. Diagnostic logs go to
stderr so the operator can confirm subprocess identity without polluting
the JSON-RPC channel.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from servers.registry.tools import (
    RACKET_BIN,
    SHOPTALK_REPO_PATH,
    query_press_registry,
    query_stock_registry,
)


# Self-announce identity to stderr before MCP takes over stdin/stdout.
print(
    f"[registry_server] PID={os.getpid()} "
    f"SHOPTALK_REPO_PATH={SHOPTALK_REPO_PATH} "
    f"RACKET_BIN={RACKET_BIN!r}",
    file=sys.stderr,
    flush=True,
)


mcp = FastMCP("registry_server")
mcp.tool()(query_stock_registry)
mcp.tool()(query_press_registry)


if __name__ == "__main__":
    mcp.run()
