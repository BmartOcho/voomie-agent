"""
seed_db.py — populate the Voomie demo database with realistic-but-fictional
customers, job history, conversations, and flags.

Usage:
    python scripts/seed_db.py            # additive seed (skips existing emails)
    python scripts/seed_db.py --wipe     # wipe all five collections, then seed

Requires: MONGODB_URI exported in the environment.

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
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Importing the server module triggers _connect() at module load. If
# MONGODB_URI is missing or auth fails, that surfaces here with a clear
# stderr message before any seed work begins.
from tools import mongodb_server as mdb  # noqa: E402


# ---------------------------------------------------------------------------
# Seed fixture data
# ---------------------------------------------------------------------------

NOW = datetime.utcnow()


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


# Each entry: customer profile, then job history. Job history is "shape-
# realistic" — declaration_source and action_plan are plausible stubs that
# parse-shape but aren't run through the Racket parser. The point is to
# give the dashboard texture and exercise the cross-collection links.

CUSTOMERS: list[dict[str, Any]] = [
    {
        "name": "Chris Walton",
        "email": "chris@blastmailco.com",
        "phone": "555-0123",
        "notes": (
            "Returning postcard customer; runs direct-mail campaigns. "
            "Knows our stock catalog by name. Prefers 100# Gloss Cover."
        ),
        "jobs": [
            {
                "job_id_suffix": "01",
                "phase": "done",
                "status": "done",
                "rush": False,
                "due_offset_days": -42,
                "created_offset_days": -56,
                "declaration_source": (
                    "#lang shoptalk\n"
                    "job \"Walton Spring Postcard\" {\n"
                    "  type:        postcard\n"
                    "  finish-size: 6in × 4in\n"
                    "  quantity:    5000\n"
                    "  stock:       100-gloss-cover\n"
                    "  press:       big-fuji\n"
                    "}\n"
                ),
                "action_plan": (
                    "(job \"Walton Spring Postcard\" "
                    "(type postcard) "
                    "(finish-size 6in 4in) "
                    "(quantity 5000) "
                    "(stock 100-gloss-cover (printiq-code 100#GlossCoverDigitalSize)))"
                ),
                "attachments_metadata": [
                    {"filename": "spring-front.pdf", "pages": 1, "color_space": "CMYK", "has_bleed": True},
                    {"filename": "spring-back.pdf", "pages": 1, "color_space": "CMYK", "has_bleed": True},
                ],
                "out_of_scope_notes": [],
                "messages": [
                    ("user", "Need 5000 4×6 postcards on the usual 100# gloss. Two-sided.", "sent"),
                    ("agent", "Customer is known; pulled 100# Gloss Cover Digital from registry.", "sent"),
                    ("agent_to_customer", "Confirming: 5000 6×4 postcards, 100# Gloss Cover, full bleed both sides. Ready to proceed?", "sent"),
                    ("user", "Yes, go ahead.", "sent"),
                ],
                "flag": None,
            },
            {
                "job_id_suffix": "02",
                "phase": "done",
                "status": "done",
                "rush": True,
                "due_offset_days": -7,
                "created_offset_days": -14,
                "declaration_source": (
                    "#lang shoptalk\n"
                    "job \"Walton Rush Postcard\" {\n"
                    "  type:        postcard\n"
                    "  finish-size: 5in × 3.5in\n"
                    "  quantity:    2500\n"
                    "  stock:       100-gloss-cover\n"
                    "  rush:        true\n"
                    "}\n"
                ),
                "action_plan": (
                    "(job \"Walton Rush Postcard\" "
                    "(type postcard) (finish-size 5in 3.5in) (quantity 2500) "
                    "(rush true))"
                ),
                "attachments_metadata": [
                    {"filename": "rush.pdf", "pages": 1, "color_space": "CMYK", "has_bleed": True},
                ],
                "out_of_scope_notes": [],
                "messages": [
                    ("user", "Rush job — need 2500 5×3.5 postcards by next Friday.", "sent"),
                    ("agent", "Validated date: 7 days out, within rush window.", "sent"),
                ],
                "flag": None,
            },
            {
                "job_id_suffix": "03",
                "phase": "ready_for_review",
                "status": "ready_for_review",
                "rush": False,
                "due_offset_days": 14,
                "created_offset_days": -1,
                "declaration_source": (
                    "#lang shoptalk\n"
                    "job \"Walton Q2 Mailer\" {\n"
                    "  type:        postcard\n"
                    "  finish-size: 6in × 4in\n"
                    "  quantity:    7500\n"
                    "  stock:       100-gloss-cover\n"
                    "}\n"
                ),
                "action_plan": (
                    "(job \"Walton Q2 Mailer\" (type postcard) "
                    "(finish-size 6in 4in) (quantity 7500))"
                ),
                "attachments_metadata": [],
                "out_of_scope_notes": [],
                "messages": [
                    ("user", "Q2 mailer: 7500 of the usual 6×4 postcards.", "sent"),
                    ("agent", "Customer known. Stock and press resolved.", "sent"),
                    ("agent_to_customer", "Q2 mailer ready: 7500 6×4 100# Gloss Cover postcards. CSR sign off?", "draft"),
                ],
                "flag": None,
            },
        ],
    },
    {
        "name": "Sandra Reyes",
        "email": "sreyes@coastalprint.com",
        "phone": "555-0144",
        "notes": (
            "New customer; professional buyer at a regional print broker. "
            "First contact via web inquiry. No prior history."
        ),
        "jobs": [],
    },
    {
        "name": "Frank Delgado",
        "email": "frank@yogaandmartialarts.com",
        "phone": "555-0188",
        "notes": (
            "Occasional customer; small studio. Often asks about mailing "
            "list services we don't offer — capture as out-of-scope."
        ),
        "jobs": [
            {
                "job_id_suffix": "01",
                "phase": "done",
                "status": "done",
                "rush": False,
                "due_offset_days": -90,
                "created_offset_days": -105,
                "declaration_source": (
                    "#lang shoptalk\n"
                    "job \"Delgado Class Schedule Postcard\" {\n"
                    "  type:        postcard\n"
                    "  finish-size: 6in × 4in\n"
                    "  quantity:    1000\n"
                    "  stock:       80-gloss-cover\n"
                    "}\n"
                ),
                "action_plan": (
                    "(job \"Delgado Class Schedule Postcard\" (type postcard) "
                    "(finish-size 6in 4in) (quantity 1000))"
                ),
                "attachments_metadata": [
                    {"filename": "class-card.pdf", "pages": 2, "color_space": "CMYK", "has_bleed": True},
                ],
                "out_of_scope_notes": [
                    "Customer asked if we could mail directly to a list of 800 addresses. "
                    "Voomie does not handle mailing services; CSR followed up offline."
                ],
                "messages": [
                    ("user", "1000 6×4 postcards. Also can you mail them to my list of 800?", "sent"),
                    ("agent", "Mailing services are out of scope — captured for CSR.", "sent"),
                    ("agent_to_customer", "Postcards: confirmed. Mailing services aren't something we offer in-house — our CSR will follow up about that part.", "sent"),
                ],
                "flag": None,
            },
            {
                "job_id_suffix": "02",
                "phase": "done",
                "status": "done",
                "rush": False,
                "due_offset_days": -30,
                "created_offset_days": -45,
                "declaration_source": (
                    "#lang shoptalk\n"
                    "job \"Delgado Open House Postcard\" {\n"
                    "  type:        postcard\n"
                    "  finish-size: 5in × 3.5in\n"
                    "  quantity:    500\n"
                    "  stock:       80-gloss-cover\n"
                    "}\n"
                ),
                "action_plan": "(job \"Delgado Open House Postcard\" (type postcard))",
                "attachments_metadata": [],
                "out_of_scope_notes": [],
                "messages": [
                    ("user", "500 of the small ones for our open house.", "sent"),
                ],
                "flag": None,
            },
        ],
    },
    {
        "name": "Cindy Park",
        "email": "cpark@campaignhq.com",
        "phone": "555-0102",
        "notes": (
            "Political-print customer; campaigns run on tight deadlines. "
            "Push cards (flat-card type) are her standard ask."
        ),
        "jobs": [
            {
                "job_id_suffix": "01",
                "phase": "done",
                "status": "done",
                "rush": True,
                "due_offset_days": -21,
                "created_offset_days": -28,
                "declaration_source": (
                    "#lang shoptalk\n"
                    "job \"Park Push Card\" {\n"
                    "  type:        flat-card\n"
                    "  finish-size: 4in × 9in\n"
                    "  quantity:    20000\n"
                    "  stock:       100-gloss-cover\n"
                    "  rush:        true\n"
                    "}\n"
                ),
                "action_plan": (
                    "(job \"Park Push Card\" (type flat-card) "
                    "(finish-size 4in 9in) (quantity 20000) (rush true))"
                ),
                "attachments_metadata": [
                    {"filename": "push-card-front.pdf", "pages": 1, "color_space": "CMYK", "has_bleed": True},
                    {"filename": "push-card-back.pdf", "pages": 1, "color_space": "CMYK", "has_bleed": True},
                ],
                "out_of_scope_notes": [],
                "messages": [
                    ("user", "20k 4×9 push cards by Wednesday. CMYK PDFs attached.", "sent"),
                    ("agent", "Date validated; rush flag set. Stock matched.", "sent"),
                ],
                "flag": None,
            },
        ],
    },
    {
        "name": "Walk-in Customer",
        "email": "walkin@voomgroup.com",
        "phone": "",
        "notes": (
            "Anonymous walk-in inquiry placeholder. Used to test the "
            "name-only lookup path in the dashboard."
        ),
        "jobs": [],
    },
]


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
    if mdb._db is None:
        print("  ✗ MongoDB not connected; cannot wipe.", file=sys.stderr)
        sys.exit(1)
    for name in ("customers", "jobs", "conversations", "flags", "seed_history"):
        result = mdb._db[name].delete_many({})
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
    if mdb._db is None:
        print(
            "✗ MongoDB connection failed at import time. "
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
