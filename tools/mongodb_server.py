"""
tools/mongodb_server.py — back-compat shim.

The real implementation lives in servers/mongodb/{tools,server}.py.
Kept as a script entry point so MCPBridge invocations using the
historical path `python tools/mongodb_server.py` keep working.

Re-exports the tool functions and selected internals (_get_db,
_get_client) so scripts that imported them from this module —
notably scripts/seed_db.py — keep working without reaching into
the new package layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from servers.mongodb.tools import (  # noqa: E402,F401
    DB_NAME,
    MONGODB_URI,
    VALID_PHASES,
    VALID_TURN_ROLES,
    VALID_TURN_STATUSES,
    _get_client,
    _get_db,
    append_conversation_turn,
    create_customer,
    flag_for_human,
    lookup_customer,
    persist_job,
    update_job_status,
)
from servers.mongodb.server import mcp  # noqa: E402


if __name__ == "__main__":
    mcp.run()
