"""
servers/parse_shoptalk/tools.py — pure tool functions for the parse_shoptalk
MCP server. No FastMCP boilerplate, no module-level side effects: importing
this module from another server (e.g. a combined voomie_server) is safe.

Per AGENT-NOTES.md (lang/shoptalk/AGENT-NOTES.md §D.1) the only supported
invocation is `racket <file.rkt>` against a temp file containing a
#lang shoptalk program. There is no `racket -l shoptalk -e ...` CLI form.
This server therefore writes the agent's source to a temp .rkt file and
shells out to Racket.

Required env vars (both optional, both have working defaults):
  SHOPTALK_REPO_PATH   Path to shoptalk repo. Default: ~/Desktop/shoptalk
  RACKET_BIN           Racket binary path. Default: /Applications/Racket v9.1/bin/racket
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SHOPTALK_REPO_PATH = (
    Path(os.environ.get("SHOPTALK_REPO_PATH", str(Path.home() / "Desktop" / "shoptalk")))
    .expanduser()
    .resolve()
)

RACKET_BIN = os.environ.get(
    "RACKET_BIN",
    "/Applications/Racket v9.1/bin/racket",
)

# First Racket invocation may compile shoptalk's modules; subsequent runs hit
# the compiled cache. 60s gives a comfortable buffer for the cold start.
PARSE_TIMEOUT_SECONDS = 60


def _classify_error(stderr: str) -> str:
    """Coarse error classification per AGENT-NOTES.md §D.2 row labels."""
    lower = stderr.lower()
    if "lexer:" in lower:
        return "lexer"
    if "encountered parsing error" in lower:
        return "parse"
    if "context...:" in stderr or "raise" in lower or " at /" in stderr:
        return "validation"
    return "other"


def parse_shoptalk(source: str) -> dict[str, Any]:
    """Parse a #lang shoptalk source program and return its action plan.

    Writes `source` to a temp .rkt file, invokes Racket, and returns either
    the action-plan s-expression (success) or a structured error dict
    (failure).

    Success shape:
      {"ok": True,
       "action_plan": "<s-expression text>",
       "warnings":    "<stderr text or empty>"}

    Failure shape:
      {"ok": False,
       "error_class": "lexer" | "parse" | "validation" | "config" | "timeout" | "other",
       "message":     "<stderr body, trimmed>",
       "exit_code":   <int>}
    """
    if not SHOPTALK_REPO_PATH.exists():
        return {
            "ok": False,
            "error_class": "config",
            "message": f"SHOPTALK_REPO_PATH does not exist: {SHOPTALK_REPO_PATH}",
            "exit_code": -1,
        }
    if not Path(RACKET_BIN).exists():
        return {
            "ok": False,
            "error_class": "config",
            "message": f"Racket binary not found at {RACKET_BIN}",
            "exit_code": -1,
        }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".rkt", delete=False, encoding="utf-8"
    ) as f:
        f.write(source)
        rkt_path = Path(f.name)

    try:
        result = subprocess.run(
            [RACKET_BIN, str(rkt_path)],
            capture_output=True,
            text=True,
            timeout=PARSE_TIMEOUT_SECONDS,
            cwd=str(SHOPTALK_REPO_PATH),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_class": "timeout",
            "message": f"Racket invocation exceeded {PARSE_TIMEOUT_SECONDS}s",
            "exit_code": -1,
        }
    finally:
        rkt_path.unlink(missing_ok=True)

    if result.returncode == 0:
        return {
            "ok": True,
            "action_plan": result.stdout.strip(),
            "warnings": result.stderr.strip(),
        }

    return {
        "ok": False,
        "error_class": _classify_error(result.stderr),
        "message": result.stderr.strip(),
        "exit_code": result.returncode,
    }
