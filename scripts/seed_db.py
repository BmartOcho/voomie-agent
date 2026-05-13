"""
seed_db.py — populate the Voomie demo database with realistic-but-fictional
customers, job history, conversations, and flags.

Usage:
    python scripts/seed_db.py            # additive seed (skips existing emails)
    python scripts/seed_db.py --wipe     # wipe all five collections, then seed

Requires: MONGODB_URI exported in the environment.

Seed data is loaded from `seed_data.json` (gitignored — see
`seed_data.json.example` for the expected shape). Each customer entry
specifies job history with `due_offset_days` and `created_offset_days`
fields that are resolved relative to NOW at seed time. The literal
declaration_source / action_plan strings round-trip through the seeder
unchanged.

Design note — why import the tool functions directly:
The seeder calls the same Python functions the FastMCP server exposes
(create_customer, persist_job, append_conversation_turn, flag_for_human).
We import them as plain Python rather than going through the MCP stdio
protocol because round-tripping JSON-RPC through a subprocess just to
talk to ourselves would add latency, ceremony, and zero validation
benefit — the tool functions are the path being exercised either way.
This also exercises the same shared-MongoClient connection that the
server uses at runtime, so any connection-handling regression shows up
here too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The mongodb tool functions live in servers/mongodb/tools.py post-refactor.
# Connection is lazy — _get_client() runs on first _get_db() call, not at
# import time, so the URI / auth check is deferred to main()'s explicit
# probe below.
from servers.mongodb import tools as mdb  # noqa: E402


# ---------------------------------------------------------------------------
# Seed fixture data
# ---------------------------------------------------------------------------

NOW = datetime.utcnow()


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def _load_customers() -> list[dict[str, Any]]:
    """Load customer + job seed data from the gitignored JSON file.

    Path override: SEED_DATA_PATH env var. Default: seed_data.json at
    the repo root. Missing file → fail loud with a pointer to the
    .example stub.
    """
    override = os.environ.get("SEED_DATA_PATH")
    path = Path(override).expanduser().resolve() if override else REPO_ROOT / "seed_data.json"
    if not path.exists():
        raise SystemExit(
            f"[seed_db] seed data not found at {path}.\n"
            f"          Copy seed_data.json.example to seed_data.json, fill in "
            f"your shop's customers + jobs, and re-run.\n"
            f"          Override the path with SEED_DATA_PATH if needed."
        )
    return json.loads(path.read_text(encoding="utf-8"))


CUSTOMERS: list[dict[str, Any]] = _load_customers()



# ---------------------------------------------------------------------------
# Seed driver
# ---------------------------------------------------------------------------


class SeedStats:
    def __init__(self) -> None:
        self.customers = 0
        self.jobs = 0
        self.turns = 0
        self.flags = 0

    def summary(self) -> str:
        return (
            f"customers={self.customers} jobs={self.jobs} "
            f"turns={self.turns} flags={self.flags}"
        )


def make_jnumber(seq: int, suffix: str) -> str:
    """Generate a deterministic J-number from a sequence and per-job suffix.

    Pattern: J + 6 digits + - + 2 digits. Six digits gives plenty of room
    for the demo set; the suffix mirrors SPEC.md's parent_id / multi-job
    children pattern (J123456-01, J123456-02).
    """
    return f"J{500000 + seq:06d}-{suffix}"


def wipe(stats: SeedStats) -> None:
    """Delete every document from all five collections.

    Uses the shared client directly — there's no MCP tool for this on
    purpose (wipe is a destructive admin op, not a runtime path).
    """
    db = mdb._get_db()
    if db is None:
        print("  ✗ MongoDB not connected; cannot wipe.", file=sys.stderr)
        sys.exit(1)
    for name in ("customers", "jobs", "conversations", "flags", "seed_history"):
        result = db[name].delete_many({})
        print(f"  · wiped {name}: {result.deleted_count} docs")


def seed_one_customer(seq: int, profile: dict[str, Any], stats: SeedStats) -> None:
    print(f"Seeding customer: {profile['name']}...")

    created = mdb.create_customer(
        name=profile["name"],
        email=profile["email"],
        phone=profile["phone"],
        notes=profile["notes"],
    )

    if not created.get("ok"):
        # Allow re-runs in additive mode: duplicate emails just mean the
        # customer is already present from a prior run. Look them up.
        if created.get("error") == "duplicate_email":
            existing = mdb.lookup_customer(profile["email"])
            if not existing.get("found"):
                print(f"  ✗ duplicate flagged but lookup failed: {existing}", file=sys.stderr)
                return
            customer_id = existing["customer"]["_id"]
            print(f"  · customer already present (id={customer_id}); skipping create")
        else:
            print(f"  ✗ create_customer failed: {created}", file=sys.stderr)
            return
    else:
        customer_id = created["customer_id"]
        stats.customers += 1
        print(f"  · created customer id={customer_id}")

    for job in profile["jobs"]:
        job_id = make_jnumber(seq, job["job_id_suffix"])
        created_at = NOW + timedelta(days=job["created_offset_days"])
        due_at = NOW + timedelta(days=job["due_offset_days"]) if job["due_offset_days"] is not None else None

        record = {
            "_id": job_id,
            "parent_id": None,
            "customer_id": customer_id,
            "status": job["status"],
            "phase": job["phase"],
            "declaration_source": job["declaration_source"],
            "action_plan": job["action_plan"],
            "attachments_metadata": job["attachments_metadata"],
            "out_of_scope_notes": job["out_of_scope_notes"],
            "due_date": due_at.isoformat() + "Z" if due_at else None,
            "rush": job["rush"],
            "created_at": created_at,
        }
        persisted = mdb.persist_job(record)
        if not persisted.get("ok"):
            print(f"  ✗ persist_job failed for {job_id}: {persisted}", file=sys.stderr)
            continue
        stats.jobs += 1
        print(f"  · persisted job {job_id} (phase={job['phase']})")

        for role, content, status in job["messages"]:
            turn = mdb.append_conversation_turn(
                job_id=job_id, role=role, content=content, status=status,
            )
            if not turn.get("ok"):
                print(f"    ✗ append turn failed: {turn}", file=sys.stderr)
                continue
            stats.turns += 1

        if job["flag"] is not None:
            flagged = mdb.flag_for_human(
                job_id=job_id,
                reason=job["flag"]["reason"],
                context=job["flag"]["context"],
            )
            if flagged.get("ok"):
                stats.flags += 1
            else:
                print(f"    ✗ flag failed: {flagged}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Voomie demo database.")
    parser.add_argument(
        "--wipe", action="store_true",
        help="Delete all documents from the five collections before seeding.",
    )
    args = parser.parse_args()

    if not os.environ.get("MONGODB_URI"):
        print("✗ MONGODB_URI not set. Export it and re-run.", file=sys.stderr)
        return 1
    if mdb._get_db() is None:
        print(
            "✗ MongoDB connection failed. "
            "Check MONGODB_URI and Atlas Database Access.",
            file=sys.stderr,
        )
        return 1

    stats = SeedStats()

    if args.wipe:
        print("Wiping collections...")
        wipe(stats)
        print()

    for seq, profile in enumerate(CUSTOMERS, start=1):
        seed_one_customer(seq, profile, stats)

    print()
    print(f"Done. {stats.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
