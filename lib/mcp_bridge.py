"""
Vertex AI ↔ MCP stdio bridge.

Spawns an MCP server as a subprocess (stdin/stdout JSON-RPC), keeps the session
alive across multiple tool calls, and exposes a synchronous `call_tool` that
a Vertex function-calling loop can invoke directly. The MCP server is torn
down cleanly when the bridge exits its context manager — including on
exceptions.

The async MCP client is driven by a dedicated background thread running its
own event loop; synchronous calls dispatch into it via
`asyncio.run_coroutine_threadsafe`. This keeps every async task in the same
task context, sidestepping the cancel-scope-cross-task error that bites when
`loop.run_until_complete` is called for the open and close phases as
independent top-level tasks.

The MCP server inherits the bridge's parent-process environment, so any env
vars the server reads (e.g. SHOPTALK_REPO_PATH, RACKET_BIN) only need to be
exported once before launching the bridge.

Usage:
    from lib.mcp_bridge import MCPBridge

    with MCPBridge(server_command="python",
                   server_args=["tools/parse_shoptalk_server.py"]) as bridge:
        result = bridge.call_tool("parse_shoptalk", {"source": "..."})
        # result: {"is_error": bool, "text": str, "structured": dict | None}
"""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPBridge:
    """Synchronous facade over an async MCP stdio session."""

    _CLOSE_TIMEOUT = 5.0  # seconds for graceful subprocess shutdown

    def __init__(
        self,
        server_command: str,
        server_args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        self._params = StdioServerParameters(
            command=server_command,
            args=server_args,
            env=env,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    def __enter__(self) -> "MCPBridge":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="MCPBridgeLoop", daemon=True
        )
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("MCPBridge event loop failed to start")
        try:
            self._session = self._submit(self._open()).result()
        except BaseException:
            self._teardown()
            raise
        return self

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._loop_ready.set)
        try:
            self._loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass

    def _submit(self, coro) -> Future:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _open(self) -> ClientSession:
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self._params)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        return session

    async def _close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None

    def list_tools(self) -> list[str]:
        if self._session is None or self._loop is None:
            raise RuntimeError("MCPBridge is not open. Use it as a context manager.")
        result = self._submit(self._session.list_tools()).result()
        return [t.name for t in result.tools]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke an MCP tool; return a Vertex-friendly response dict.

        Returned shape:
          {
            "is_error":   bool,        # True if the server flagged the call as errored
            "text":       str,         # concatenated TextContent items
            "structured": dict | None  # structuredContent if provided, else
                                       # parsed JSON from `text` if parseable
          }
        """
        if self._session is None or self._loop is None:
            raise RuntimeError("MCPBridge is not open. Use it as a context manager.")

        result = self._submit(self._session.call_tool(name, arguments)).result()

        text_parts: list[str] = []
        for item in result.content or []:
            if hasattr(item, "text") and item.text is not None:
                text_parts.append(item.text)
        text = "\n".join(text_parts)

        structured: dict | None = None
        sc = getattr(result, "structuredContent", None)
        if isinstance(sc, dict):
            structured = sc
        elif text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    structured = parsed
            except (json.JSONDecodeError, TypeError):
                structured = None

        return {
            "is_error": bool(getattr(result, "isError", False)),
            "text": text,
            "structured": structured,
        }

    def __exit__(self, exc_type, exc, tb) -> None:
        self._teardown()

    def _teardown(self) -> None:
        # Close the MCP session on the loop thread (same task context as _open).
        if self._loop is not None and not self._loop.is_closed():
            try:
                if self._exit_stack is not None:
                    fut = self._submit(self._close())
                    try:
                        fut.result(timeout=self._CLOSE_TIMEOUT)
                    except Exception:
                        pass  # best-effort; subprocess gets killed when loop ends
            finally:
                # Stop the run_forever loop.
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                except RuntimeError:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=self._CLOSE_TIMEOUT)
            self._thread = None
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.close()
            except Exception:
                pass
        self._loop = None
        self._exit_stack = None
        self._session = None
