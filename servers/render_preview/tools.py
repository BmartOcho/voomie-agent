"""
servers/render_preview/tools.py — pure tool function for the
render_preview MCP server. No FastMCP boilerplate, no module-level
side effects beyond imports.

Wraps shoptalk's Python verifier (`python -m verifier.build`) to render
an action plan to a preview PDF. The action plan is the `action_plan`
field returned by parse_shoptalk; the verifier is the same component
shoptalk uses internally to produce job preview sheets, so this is a
real round-trip through the shoptalk pipeline rather than a separate
renderer.

Subprocess contract:
    python -m verifier.build <input.txt> <output.pdf>
    stdout: "Written: <output.pdf>" on success
    exit 0 on success, non-zero with a traceback on stderr otherwise

The verifier reads the action plan from a file path (argv[1]) — not stdin
— so we write it to a temp file, hand the path over, and clean up the
input temp file in a finally block whatever happens. The output PDF
survives; it's the deliverable.

Required env vars (required — no default; missing values fail loud at
call time with a config-error envelope):
  SHOPTALK_REPO_PATH   Path to the shoptalk repo (the verifier's working dir)

We deliberately use sys.executable (not a hardcoded "python3") so the
verifier's deps are picked up from whatever Python environment the MCP
server itself is running under. Mismatched interpreters between server
and verifier would surface as ImportErrors inside the subprocess — easy
to diagnose, but easier to avoid.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


_SHOPTALK_REPO_PATH_RAW = os.environ.get("SHOPTALK_REPO_PATH")
SHOPTALK_REPO_PATH = (
    Path(_SHOPTALK_REPO_PATH_RAW).expanduser().resolve()
    if _SHOPTALK_REPO_PATH_RAW
    else None
)

# Verifier is a pure-Python reportlab renderer; cold start is fast. 30 s
# leaves headroom for a slow filesystem and any future complexity in
# build.py without masking a hung subprocess.
RENDER_TIMEOUT_SECONDS = 30


def _config_check() -> dict[str, Any] | None:
    """Return a config-error envelope if pre-conditions fail; else None."""
    if SHOPTALK_REPO_PATH is None:
        return {
            "ok": False,
            "error_class": "not_found",
            "message": "SHOPTALK_REPO_PATH environment variable is not set",
            "stderr": "",
        }
    if not SHOPTALK_REPO_PATH.exists():
        return {
            "ok": False,
            "error_class": "not_found",
            "message": f"SHOPTALK_REPO_PATH does not exist: {SHOPTALK_REPO_PATH}",
            "stderr": "",
        }
    verifier_init = SHOPTALK_REPO_PATH / "verifier" / "build.py"
    if not verifier_init.exists():
        return {
            "ok": False,
            "error_class": "not_found",
            "message": f"verifier.build not found at {verifier_init}",
            "stderr": "",
        }
    return None


def _resolve_output_path(output_path: str) -> Path:
    """Pick an output path for the rendered PDF.

    Empty string or unset → /tmp/voomie-preview-<uuid>.pdf.
    Provided path → used as-is, parent directories created on demand so
    the agent doesn't have to mkdir before calling.
    """
    if not output_path:
        return Path(tempfile.gettempdir()) / f"voomie-preview-{uuid.uuid4().hex}.pdf"
    p = Path(output_path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def render_preview(action_plan: str, output_path: str = "") -> dict[str, Any]:
    """Render a shoptalk action plan to a preview PDF using the shoptalk
    verifier. Call this with the action_plan field from a successful
    parse_shoptalk result. Returns the path to the rendered PDF. Use this
    for the booklet preview beat in the demo — shows the full machine-
    readable contract downstream.

    Argument:
      action_plan — the structured plan text returned in parse_shoptalk's
                    `action_plan` field. Must be the parser's output
                    verbatim; hand-edited plans will fail because the
                    verifier expects parser-stamped fields.
      output_path — optional absolute path for the rendered PDF. Empty
                    string → temp path under the OS temp dir.

    Success shape:
      {ok: true, output_path: <str>, file_size_bytes: <int>}

    Failure shape:
      {ok: false,
       error_class: "render_error" | "not_found" | "internal_error" | "timeout",
       message: <str>,
       stderr: <str>}
    """
    cfg_err = _config_check()
    if cfg_err is not None:
        print(
            f"[render_preview_server] config error: {cfg_err['message']}",
            file=sys.stderr,
            flush=True,
        )
        return cfg_err

    if not isinstance(action_plan, str) or not action_plan.strip():
        return {
            "ok": False,
            "error_class": "render_error",
            "message": "action_plan is empty",
            "stderr": "",
        }

    out_path = _resolve_output_path(output_path)

    # Write the plan to a temp file the verifier can read by path.
    # delete=False because the subprocess opens the file by name; we
    # delete it ourselves in the finally block.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8",
        prefix="voomie-plan-",
    ) as f:
        f.write(action_plan)
        plan_path = Path(f.name)

    print(
        f"[render_preview_server] invoking verifier: "
        f"plan={plan_path} → output={out_path} "
        f"(plan_bytes={plan_path.stat().st_size})",
        file=sys.stderr,
        flush=True,
    )

    try:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "verifier.build", str(plan_path), str(out_path)],
                cwd=str(SHOPTALK_REPO_PATH),
                capture_output=True,
                text=True,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            print(
                f"[render_preview_server] verifier timeout after "
                f"{RENDER_TIMEOUT_SECONDS}s",
                file=sys.stderr, flush=True,
            )
            return {
                "ok": False,
                "error_class": "timeout",
                "message": f"verifier exceeded {RENDER_TIMEOUT_SECONDS}s",
                "stderr": "",
            }

        print(
            f"[render_preview_server] verifier exit={result.returncode} "
            f"stdout_len={len(result.stdout)} stderr_len={len(result.stderr)}",
            file=sys.stderr, flush=True,
        )

        if result.returncode != 0:
            # The verifier traceback goes to stderr; the agent will read it
            # and either retry or escalate. Don't try to classify subtypes
            # here — the message is human-readable Python and that's the
            # most useful thing to surface.
            return {
                "ok": False,
                "error_class": "render_error",
                "message": f"Verifier exited with code {result.returncode}",
                "stderr": result.stderr.strip(),
            }

        if not out_path.exists():
            # Defensive: success exit code but no file on disk would mean
            # the verifier silently swallowed an error. Surface it
            # explicitly rather than reporting a phantom success.
            return {
                "ok": False,
                "error_class": "internal_error",
                "message": (
                    "verifier exited 0 but output PDF is missing at "
                    f"{out_path}"
                ),
                "stderr": result.stderr.strip(),
            }

        size = out_path.stat().st_size
        print(
            f"[render_preview_server] success: {out_path} ({size} bytes)",
            file=sys.stderr, flush=True,
        )
        return {
            "ok": True,
            "output_path": str(out_path),
            "file_size_bytes": size,
        }
    finally:
        # Output PDF stays — that's the deliverable. Input plan file is
        # always cleaned up regardless of success/failure.
        plan_path.unlink(missing_ok=True)
