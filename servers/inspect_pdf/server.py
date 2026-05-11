"""
servers/inspect_pdf/server.py — FastMCP standalone server for the
inspect_pdf tool. Composes FastMCP boilerplate around the pure tool
function in servers/inspect_pdf/tools.py.

Stdio: JSON-RPC framed by MCP on stdin/stdout. Diagnostic logs go to
stderr so the operator can confirm subprocess identity without polluting
the JSON-RPC channel — same convention as the other Voomie MCP servers.
"""

from __future__ import annotations

import os
import sys

import fitz  # PyMuPDF — imported here for the version banner
from mcp.server.fastmcp import FastMCP

from servers.inspect_pdf.tools import inspect_pdf


# Self-announce identity to stderr before MCP takes over stdin/stdout.
print(
    f"[inspect_pdf_server] PID={os.getpid()} "
    f"fitz={fitz.__version__}",
    file=sys.stderr,
    flush=True,
)


mcp = FastMCP("inspect_pdf_server")
mcp.tool()(inspect_pdf)


if __name__ == "__main__":
    mcp.run()
