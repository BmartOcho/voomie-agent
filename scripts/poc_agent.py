"""
poc_agent.py — full-loop Voomie demo on Chris Walton's multi-job message.

This is the screen-recording target for the SPEC.md §Demo arc 0:20–1:50
beat: paste a real customer message, watch Voomie classify it as
multi-job, surface the spot UV + soft-touch laminate coating conflict,
and persist the resulting declarations to MongoDB.

The output format is shaped for projection during a 3-minute demo:
clear section banners, indented tool-call traces, and a summary box at
the end. Stdout is the structured result JSON; everything else goes to
stderr (matching voomie/cli.py's discipline).

Run:
  export MONGODB_URI=...
  export GCP_PROJECT_ID=...
  export SHOPTALK_REPO_PATH=...
  python scripts/poc_agent.py

Pre-req: scripts/seed_db.py has been run so Chris is in the customer
collection — the "returning customer" beat depends on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from voomie.agent import process_message  # noqa: E402


CHRIS_EMAIL = "chris@blastmailco.com"

CHRIS_MESSAGE = """\
Hi team — two jobs this week.

1) BLAST mailer: 5,000 6×4 postcards, 4/4 bleed, 100# Gloss Cover, mail
   class same as last time. Need them on the truck by next Friday.

2) Valentine card: 1,000 5×3.5 postcards, 4/4 bleed, 16pt C2S. I want
   spot UV on the front (the heart graphic) AND soft-touch laminate
   over the whole face — make it really pop while still feeling
   premium. CMYK PDF coming separately for both.

Thanks,
Chris
"""


def _banner(text: str) -> None:
    """Section header — kept simple for legibility under projector contrast."""
    print("", file=sys.stderr, flush=True)
    print("=" * 78, file=sys.stderr, flush=True)
    print(f"  {text}", file=sys.stderr, flush=True)
    print("=" * 78, file=sys.stderr, flush=True)


def _check_env() -> None:
    """Fail fast on missing creds. Demos pause for nothing more annoying
    than discovering 30 seconds in that MONGODB_URI was unset."""
    required = ("MONGODB_URI", "GCP_PROJECT_ID", "SHOPTALK_REPO_PATH")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n[poc_agent] missing required env vars: {missing}", file=sys.stderr)
        print(
            f"[poc_agent] export them before running. See voomie/agent.py for defaults.",
            file=sys.stderr,
        )
        sys.exit(2)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Single optional knob: which Gemini model to run.

    Defaults to whatever voomie.agent picks (gemini-2.5-pro unless
    VOOMIE_MODEL is set), so the canonical demo command stays
    `python scripts/poc_agent.py` with no flags.
    """
    parser = argparse.ArgumentParser(
        prog="poc_agent",
        description=(
            "Run the full Voomie agent loop on Chris Walton's multi-job "
            "message and print a JSON result on stdout."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Gemini model name (e.g. 'gemini-2.5-pro' or 'gemini-2.5-flash'). "
            "Defaults to VOOMIE_MODEL or voomie.agent's built-in default."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    _check_env()

    _banner("Voomie agent loop POC — Chris Walton's multi-job message")
    print("", file=sys.stderr)
    print("Customer:", CHRIS_EMAIL, file=sys.stderr)
    if args.model:
        print(f"Model:    {args.model}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Message:", file=sys.stderr)
    print("-" * 78, file=sys.stderr)
    print(CHRIS_MESSAGE, file=sys.stderr)
    print("-" * 78, file=sys.stderr)

    _banner("Spawning MCP server, running agent loop...")
    print(
        "(streaming agent log to stderr; final JSON result follows on stdout)",
        file=sys.stderr,
    )

    result = process_message(
        customer_query=CHRIS_EMAIL,
        message_text=CHRIS_MESSAGE,
        attachments=None,
        model_name=args.model,
    )

    _banner("Agent loop complete — summary")
    print("", file=sys.stderr)
    print(f"  ok                      : {result.get('ok')}", file=sys.stderr)
    if result.get("ok"):
        print(f"  parent J-number         : {result['parent_job_id']}", file=sys.stderr)
        print(
            f"  child J-numbers ({len(result['child_job_ids'])})       : "
            f"{result['child_job_ids']}",
            file=sys.stderr,
        )
        print(
            f"  final status            : {result['final_status']}",
            file=sys.stderr,
        )
        print(
            f"  declarations produced   : {result['declarations_produced']}",
            file=sys.stderr,
        )
        print(
            f"  flags raised            : {result['flags_raised']}",
            file=sys.stderr,
        )
        print(
            f"  elapsed                 : {result['elapsed_seconds']}s",
            file=sys.stderr,
        )
        # New post-fix counters; old runs returned dicts without these
        # keys, so default to '-' for back-compat with any captured JSON.
        print(
            f"  model                   : {result.get('model', '-')}",
            file=sys.stderr,
        )
        print(
            f"  typo corrections        : "
            f"{result.get('typo_corrections_applied', '-')}",
            file=sys.stderr,
        )
        print(
            f"  malformed turns (total) : "
            f"{result.get('malformed_turns_total', '-')}",
            file=sys.stderr,
        )
        print(
            f"  malformed recovered     : "
            f"{result.get('malformed_turns_recovered', '-')}",
            file=sys.stderr,
        )
    else:
        print(f"  error                   : {result.get('error')}", file=sys.stderr)
        print(
            f"  parent J-number         : {result.get('parent_job_id')}",
            file=sys.stderr,
        )
    print("", file=sys.stderr)

    # Structured result on stdout — pipeable into jq, dashboard subprocess,
    # or recording-day captures.
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[poc_agent] interrupted by user.", file=sys.stderr)
        sys.exit(130)
