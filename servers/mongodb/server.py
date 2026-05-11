"""
servers/mongodb/server.py — FastMCP standalone server for the mongodb
tools. Composes FastMCP boilerplate around the pure tool functions in
servers/mongodb/tools.py.

Connection is lazy: the first tool call to touch the database triggers
_get_client(); the resulting MongoClient is cached process-wide via
lru_cache. The startup banner only reports whether MONGODB_URI is set,
not whether the connection succeeded — a misconfigured URI surfaces as
{ok: false, error: "mongodb_unavailable"} on the first call.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from servers.mongodb.tools import (
    DB_NAME,
    MONGODB_URI,
    append_conversation_turn,
    create_customer,
    flag_for_human,
    lookup_customer,
    persist_job,
    update_job_status,
)


# Self-announce identity to stderr before MCP takes over stdin/stdout.
print(
    f"[mongodb_server] PID={os.getpid()} db={DB_NAME!r} "
    f"uri_configured={bool(MONGODB_URI)}",
    file=sys.stderr,
    flush=True,
)


mcp = FastMCP("mongodb_server")
mcp.tool()(lookup_customer)
mcp.tool()(create_customer)
mcp.tool()(update_job_status)
mcp.tool()(append_conversation_turn)
mcp.tool()(persist_job)
mcp.tool()(flag_for_human)


if __name__ == "__main__":
    mcp.run()
