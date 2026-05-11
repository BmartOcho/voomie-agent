"""
tools/inspect_pdf_server.py — back-compat shim.

The real implementation lives in servers/inspect_pdf/{tools,server}.py.
Kept as a script entry point so MCPBridge invocations using the
historical path `python tools/inspect_pdf_server.py` keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from servers.inspect_pdf.tools import inspect_pdf  # noqa: E402,F401
from servers.inspect_pdf.server import mcp  # noqa: E402


if __name__ == "__main__":
    mcp.run()
