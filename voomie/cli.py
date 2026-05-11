"""
voomie/cli.py — thin CLI wrapper around process_message().

Used by:
  • Direct operator invocation for sanity checks
    (`python -m voomie.cli --customer x@y.com --message "..."`)
  • The CSR dashboard's "paste a message" path (subprocess invocation
    when the dashboard is wired up)
  • The demo recording fallback (canned messages from a file)

Stdout is reserved for the result JSON so callers can pipe it into jq;
all human-readable progress goes to stderr (via voomie.agent's _log).
Exit code 0 on agent success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python -m voomie.cli` and `python voomie/cli.py` both to work.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from voomie.agent import process_message  # noqa: E402


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="voomie",
        description=(
            "Run the Voomie agent on a single customer message. "
            "Prints a JSON result to stdout; logs to stderr."
        ),
    )
    parser.add_argument(
        "--customer",
        required=True,
        help="Customer identifier (email preferred, name accepted).",
    )
    msg_group = parser.add_mutually_exclusive_group(required=True)
    msg_group.add_argument(
        "--message",
        help="Customer message text passed inline.",
    )
    msg_group.add_argument(
        "--message-file",
        help="Path to a file containing the customer message text.",
    )
    parser.add_argument(
        "--attachments",
        nargs="*",
        default=None,
        help="Optional list of file paths attached to the message (PDFs).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Gemini model name (e.g. 'gemini-2.5-pro' or "
            "'gemini-2.5-flash'). Defaults to VOOMIE_MODEL or "
            "voomie.agent's built-in default ('gemini-2.5-pro')."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.message is not None:
        message_text = args.message
    else:
        path = Path(args.message_file)
        if not path.exists():
            print(
                json.dumps({"ok": False, "error": f"message_file not found: {path}"}),
                file=sys.stdout,
            )
            return 1
        message_text = path.read_text(encoding="utf-8")

    result = process_message(
        customer_query=args.customer,
        message_text=message_text,
        attachments=args.attachments,
        model_name=args.model,
    )

    # Pretty-printed JSON for human readers; jq users can re-flatten.
    print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout, flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
