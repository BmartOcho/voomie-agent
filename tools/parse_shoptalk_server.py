"""
tools/parse_shoptalk_server.py — back-compat shim.

The real implementation lives in servers/parse_shoptalk/{tools,server}.py.
This shim exists so that:
  - MCPBridge subprocess invocations using the historical path
    `python tools/parse_shoptalk_server.py` keep working.
  - Direct imports like `from tools.parse_shoptalk_server import parse_shoptalk`
    keep resolving to the same function (used by tests/test_render_preview_bridge.py).

The sys.path tweak is needed because Python's default sys.path puts the
script's directory first when invoked as `python <path>`, which would
hide the top-level `servers/` package from this shim.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from servers.parse_shoptalk.tools import parse_shoptalk  # noqa: E402,F401
from servers.parse_shoptalk.server import mcp  # noqa: E402


if __name__ == "__main__":
    mcp.run()
