"""
servers/parse_shoptalk/server.py — FastMCP standalone server for the
parse_shoptalk tool. Composes the FastMCP boilerplate around the pure
tool function in servers/parse_shoptalk/tools.py.

Stdio: JSON-RPC framed by MCP on stdin/stdout. Diagnostic logs go to stderr
(visible in the parent terminal) so the operator can confirm subprocess
identity and see warnings without polluting the JSON-RPC channel.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from servers.parse_shoptalk.tools import (
    RACKET_BIN,
    SHOPTALK_REPO_PATH,
    parse_shoptalk,
)


# Self-announce identity to stderr before MCP takes over stdin/stdout. The
# bridge inherits stderr by default, so this prints to the operator's terminal.
print(
    f"[parse_shoptalk_server] PID={os.getpid()} "
    f"SHOPTALK_REPO_PATH={SHOPTALK_REPO_PATH} "
    f"RACKET_BIN={RACKET_BIN!r}",
    file=sys.stderr,
    flush=True,
)


mcp = FastMCP("parse_shoptalk_server")
mcp.tool()(parse_shoptalk)


if __name__ == "__main__":
    mcp.run()
