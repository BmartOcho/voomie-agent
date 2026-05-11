"""
tools/render_preview_server.py — back-compat shim.

The real implementation lives in servers/render_preview/{tools,server}.py.
Kept as a script entry point so MCPBridge invocations using the
historical path `python tools/render_preview_server.py` keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from servers.render_preview.tools import render_preview  # noqa: E402,F401
from servers.render_preview.server import mcp  # noqa: E402


if __name__ == "__main__":
    mcp.run()
