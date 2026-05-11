"""
servers/registry/tools.py — pure tool functions for the registry MCP
server (query_stock_registry, query_press_registry). No FastMCP
boilerplate, no module-level side effects beyond imports and constants.

Both tools shell out to the same Racket CLI, sending a one-query batch on
stdin and parsing the per-query envelope from stdout. The CLI's JSON
contract is locked (see shoptalk's tests/test_query_registry.py and
lang/shoptalk/REGISTRY-RECON.md):

    Input shape:
      {"queries": [{"kind": "stock"|"press", "criteria": {...}, "limit": N}]}

    Success (exit 0):
      {"results": [{"kind": "stock", "count": N, "results": [<record>...]}, ...]}

    Error (exit non-zero):
      {"error": true,
       "error_class": "criteria-error" | "registry-error" | "internal-error",
       "message": "...",
       "query_index": <int or null>}

Per-call response shape (flattened from the CLI's envelope):
    Success:  {ok: true,  kind, count, results: [...]}
    Failure:  {ok: false, error_class, message, query_index, exit_code}

Required env vars (both optional, both have working defaults):
  SHOPTALK_REPO_PATH   Path to shoptalk repo. Default: ~/Desktop/shoptalk
  RACKET_BIN           Racket binary path. Default: /Applications/Racket v9.1/bin/racket

----- Future amortization (out of scope here, intentional) -----
Each Gemini tool call currently spawns a fresh Racket subprocess and pays
~250-310 ms cold-load (per shoptalk's REGISTRY-RECON.md §5). If a single
agent turn issues >5 registry queries and the demo's 90s target is at
risk, two mitigations exist:
  (a) Batch at the MCP server layer — collect calls within a short window,
      flush as one Racket invocation. Requires changing the tool's per-call
      contract (Gemini sends one query at a time).
  (b) Keep a long-lived Racket REPL process and pipe queries to it.
      Eliminates cold-start entirely after the first query.
Trigger condition: revisit when 5+ queries per turn become routine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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

QUERY_CLI_RELATIVE = Path("tools") / "query-registry.rkt"

# Cold-load cost is ~250-310 ms; 10 s leaves comfortable headroom for any
# transient slowness without masking a hung subprocess.
QUERY_TIMEOUT_SECONDS = 10


def _config_check() -> dict[str, Any] | None:
    """Return a config-error envelope if pre-conditions fail; else None."""
    if not SHOPTALK_REPO_PATH.exists():
        return {
            "ok": False,
            "error_class": "config",
            "message": f"SHOPTALK_REPO_PATH does not exist: {SHOPTALK_REPO_PATH}",
            "query_index": None,
            "exit_code": -1,
        }
    if not Path(RACKET_BIN).exists():
        return {
            "ok": False,
            "error_class": "config",
            "message": f"Racket binary not found at {RACKET_BIN}",
            "query_index": None,
            "exit_code": -1,
        }
    cli_path = SHOPTALK_REPO_PATH / QUERY_CLI_RELATIVE
    if not cli_path.exists():
        return {
            "ok": False,
            "error_class": "config",
            "message": f"query-registry.rkt not found at {cli_path}",
            "query_index": None,
            "exit_code": -1,
        }
    return None


def _run_single_query(
    kind: str, criteria: dict[str, Any], limit: int
) -> dict[str, Any]:
    """Invoke the Racket CLI with a one-query batch; return a flat envelope.

    On success returns {ok: True, kind, count, results}. On any failure
    (subprocess error, malformed CLI output, criteria-error JSON from the
    CLI, timeout, missing config) returns {ok: False, error_class, message,
    query_index, exit_code}.
    """
    cfg_err = _config_check()
    if cfg_err is not None:
        print(f"[registry_server] config error: {cfg_err['message']}", file=sys.stderr, flush=True)
        return cfg_err

    batch = {
        "queries": [
            {"kind": kind, "criteria": criteria, "limit": limit},
        ]
    }
    stdin_payload = json.dumps(batch)

    print(
        f"[registry_server] invoking racket: kind={kind} "
        f"criteria_keys={sorted(criteria.keys())} limit={limit}",
        file=sys.stderr,
        flush=True,
    )

    try:
        proc = subprocess.run(
            [RACKET_BIN, str(QUERY_CLI_RELATIVE)],
            input=stdin_payload,
            cwd=str(SHOPTALK_REPO_PATH),
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[registry_server] timeout after {QUERY_TIMEOUT_SECONDS}s",
            file=sys.stderr,
            flush=True,
        )
        return {
            "ok": False,
            "error_class": "timeout",
            "message": f"Racket invocation exceeded {QUERY_TIMEOUT_SECONDS}s",
            "query_index": None,
            "exit_code": -1,
        }

    print(
        f"[registry_server] subprocess done: exit={proc.returncode} "
        f"stdout_len={len(proc.stdout)} stderr_len={len(proc.stderr)}",
        file=sys.stderr,
        flush=True,
    )

    raw_stdout = proc.stdout.strip()
    if not raw_stdout:
        print(
            f"[registry_server] empty stdout; stderr={proc.stderr!r}",
            file=sys.stderr,
            flush=True,
        )
        return {
            "ok": False,
            "error_class": "internal-error",
            "message": (
                f"empty stdout from query-registry.rkt; stderr: {proc.stderr.strip()}"
            ),
            "query_index": None,
            "exit_code": proc.returncode,
        }

    try:
        parsed = json.loads(raw_stdout)
    except json.JSONDecodeError as e:
        print(
            f"[registry_server] JSON decode failed: {e}; raw={raw_stdout!r}",
            file=sys.stderr,
            flush=True,
        )
        return {
            "ok": False,
            "error_class": "internal-error",
            "message": f"could not parse CLI stdout as JSON: {e}",
            "query_index": None,
            "exit_code": proc.returncode,
        }

    # Error path: CLI emits a single object with error=true.
    if isinstance(parsed, dict) and parsed.get("error") is True:
        print(
            f"[registry_server] CLI returned error: "
            f"class={parsed.get('error_class')!r} "
            f"qi={parsed.get('query_index')!r}",
            file=sys.stderr,
            flush=True,
        )
        return {
            "ok": False,
            "error_class": str(parsed.get("error_class", "unknown")),
            "message": str(parsed.get("message", "")),
            "query_index": parsed.get("query_index"),
            "exit_code": proc.returncode,
        }

    # Success path: CLI emits {"results": [<envelope>, ...]}.
    if not isinstance(parsed, dict) or "results" not in parsed:
        return {
            "ok": False,
            "error_class": "internal-error",
            "message": f"CLI output missing 'results' key: {parsed!r}",
            "query_index": None,
            "exit_code": proc.returncode,
        }
    envelopes = parsed["results"]
    if not isinstance(envelopes, list) or len(envelopes) != 1:
        return {
            "ok": False,
            "error_class": "internal-error",
            "message": (
                f"expected exactly one result envelope; got {len(envelopes) if isinstance(envelopes, list) else type(envelopes).__name__}"
            ),
            "query_index": None,
            "exit_code": proc.returncode,
        }

    env = envelopes[0]
    print(
        f"[registry_server] success: kind={env.get('kind')} count={env.get('count')}",
        file=sys.stderr,
        flush=True,
    )
    return {
        "ok": True,
        "kind": env.get("kind"),
        "count": env.get("count"),
        "results": env.get("results", []),
    }


def query_stock_registry(
    criteria: dict[str, Any] | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """Search the print shop's actual stock inventory. Use this instead of
    guessing what stocks exist — the registry is the source of truth, and
    every record returned has a real PrintIQ code that the parser will
    accept downstream.

    WHEN TO CALL:
      • A customer mentions a paper, stock, or substrate of any kind, even
        in fuzzy or approximate language ("something like 80# cover", "the
        heavy gloss stock", "around 100lb matte"). Do not parse fuzzy
        language yourself — pass it through `text_search` and let the
        registry resolve it.
      • A customer specifies precise stock specifications ("100# Gloss
        Cover", "16pt C2S"). These also go through `text_search`; the
        registry handles both fuzzy and precise matching internally.
      • You are about to emit a shoptalk declaration that names a stock
        and you want to confirm it exists.

    CRITERIA:
      • text_search (str) — Free text. Resolves stock names, aliases, and
        substring matches. Send the customer's language verbatim or
        lightly normalized.
      • basis_weight_min (number) — Lower bound on basis weight (in the
        stock's own basis units, e.g. 80 for 80lb cover).
      • basis_weight_max (number) — Upper bound on basis weight.
      • coating ("coated" | "uncoated" | "any") — Filter by coating.
      • finish (str) — Filter by finish (e.g. "gloss", "matte", "silk").

      Combine `text_search` with structural filters only when the customer
      specifies multiple constraints. For most stock lookups, a single
      `text_search` is the right call.

    MATCH TIER (each result includes `match_tier`):
      • "exact" / "alias"     — confident match. Use directly.
      • "ambiguous"           — multiple candidates resolved equally; ask
                                the customer to disambiguate before
                                committing to one.
      • "name-substring" /
        "alias-substring" /
        "token-overlap"       — fuzzy fall-through; treat as suggestions,
                                offer them as options to the customer.

    LIMIT (default 3):
      • Use 1 when you are confident about the resolution and just want
        the canonical record.
      • Use 3-5 when offering options to the customer.
      • Higher limits are fine for browsing.

    Returns on success:
      {ok: true, kind: "stock", count: <int>, results: [<record>, ...]}

    Returns on error:
      {ok: false, error_class: "criteria-error" | "registry-error" |
       "internal-error" | "config" | "timeout",
       message: <str>, query_index: <int|null>, exit_code: <int>}
    """
    return _run_single_query("stock", criteria or {}, limit)


def query_press_registry(
    criteria: dict[str, Any] | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """Search the print shop's actual press inventory. Use this to confirm
    a press exists by name, alias, or shortname before committing to it
    in a shoptalk declaration, and to filter presses by format.

    WHEN TO CALL:
      • A customer mentions a press by name, alias, or shortname ("Big
        Fuji", "the Fuji", "the wide-format printer").
      • You need to know which presses are available for a given format
        (sheet-fed vs. wide-format).

    CRITERIA:
      • text_search (str) — Free text. Resolves press names, aliases,
        and shortnames.
      • format ("sheet" | "wide-format" | "any") — Filter by press
        category. "sheet" returns digital and offset sheetfed plus
        envelope presses; "wide-format" returns roll-fed and large-format.

    MATCH TIER (each result includes `match_tier` when text_search is
    used):
      • "exact" / "alias"     — confident match. Use directly.
      • "ambiguous"           — multiple candidates; ask the customer.
      • "name-substring" /
        "alias-substring" /
        "token-overlap"       — fuzzy suggestions.

    LIMIT (default 3):
      • Use 1 for confident lookups; 3-5 to offer options.

    Returns on success:
      {ok: true, kind: "press", count: <int>, results: [<record>, ...]}

    Returns on error:
      {ok: false, error_class: "criteria-error" | "registry-error" |
       "internal-error" | "config" | "timeout",
       message: <str>, query_index: <int|null>, exit_code: <int>}
    """
    return _run_single_query("press", criteria or {}, limit)
