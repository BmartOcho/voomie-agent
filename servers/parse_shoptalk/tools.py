"""
servers/parse_shoptalk/tools.py — pure tool functions for the parse_shoptalk
MCP server. No FastMCP boilerplate, no module-level side effects: importing
this module from another server (e.g. a combined voomie_server) is safe.

Writes the agent's spec source to a temp file and shells out to shoptalk's
spec parser CLI, which is configured via env vars at call time.

Required env vars (both required — no defaults; missing values fail loud
at call time with a config-error envelope):
  SHOPTALK_REPO_PATH   Path to the shoptalk repo (the parser's working dir)
  RACKET_BIN           Path to the parser binary
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


_SHOPTALK_REPO_PATH_RAW = os.environ.get("SHOPTALK_REPO_PATH")
SHOPTALK_REPO_PATH = (
    Path(_SHOPTALK_REPO_PATH_RAW).expanduser().resolve()
    if _SHOPTALK_REPO_PATH_RAW
    else None
)

RACKET_BIN = os.environ.get("RACKET_BIN")

# First parser invocation may compile shoptalk's modules; subsequent runs
# hit the compiled cache. 60s gives a comfortable buffer for the cold start.
PARSE_TIMEOUT_SECONDS = 60


# Substring tells the parser emits on different error categories.
# Kept opaque on purpose — these are tied to the parser's diagnostic
# output and change there, not here.
_ERR_TELL_LEXER = "lexer:"
_ERR_TELL_PARSE = "encountered parsing error"
_ERR_TELL_VALIDATION_A = "context...:"
_ERR_TELL_VALIDATION_B = "raise"
_ERR_TELL_VALIDATION_C = " at /"


def _classify_error(stderr: str) -> str:
    """Coarse error classification based on parser diagnostic tells."""
    lower = stderr.lower()
    if _ERR_TELL_LEXER in lower:
        return "lexer"
    if _ERR_TELL_PARSE in lower:
        return "parse"
    if (
        _ERR_TELL_VALIDATION_A in stderr
        or _ERR_TELL_VALIDATION_B in lower
        or _ERR_TELL_VALIDATION_C in stderr
    ):
        return "validation"
    return "other"


def parse_shoptalk(source: str) -> dict[str, Any]:
    """Parse a shoptalk spec source program and return its action plan.

    Writes `source` to a temp file, invokes the parser binary, and returns
    either the structured action plan (success) or a structured error dict
    (failure).

    Success shape:
      {"ok": True,
       "action_plan": "<structured plan text>",
       "warnings":    "<stderr text or empty>"}

    Failure shape:
      {"ok": False,
       "error_class": "lexer" | "parse" | "validation" | "config" | "timeout" | "other",
       "message":     "<stderr body, trimmed>",
       "exit_code":   <int>}
    """
    if SHOPTALK_REPO_PATH is None:
        return {
            "ok": False,
            "error_class": "config",
            "message": "SHOPTALK_REPO_PATH environment variable is not set",
            "exit_code": -1,
        }
    if RACKET_BIN is None:
        return {
            "ok": False,
            "error_class": "config",
            "message": "RACKET_BIN environment variable is not set",
            "exit_code": -1,
        }
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
            "message": f"Parser binary not found at {RACKET_BIN}",
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
            "message": f"Parser invocation exceeded {PARSE_TIMEOUT_SECONDS}s",
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
