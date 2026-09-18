r"""
OmniTrack AI — Audit Chain Maintenance
══════════════════════════════════════

The audit log is a SHA-256 hash chain: every row's `current_hash` covers its own
fields plus the previous row's hash, so editing or removing any past row breaks
every hash after it. The proposal's criterion is 100% tamper detection.

This CLI is the operational side of that:

  --verify   Check the live chain (linkage + content) and print the first break.
  --forks    List rows that share a previous_hash, i.e. concurrent-append races.
  --reset    Archive the current chain and start a fresh one from genesis.
  --harden   Add a UNIQUE index on previous_hash so a fork becomes impossible
             at the storage layer, not just unlikely. Requires a fork-free chain.

Run:
  cd backend
  ..\.venv\Scripts\python.exe scripts\audit_chain.py --verify

Why --reset exists
------------------
A hash chain cannot be repaired. Recomputing hashes to "fix" a break is exactly
the rewrite a tamper-evident log is designed to make detectable, so a tool that
did it would defeat the feature it maintains. The only honest options are to
leave the break visible or to archive the old chain and start a new one, which
is what --reset does: rows are copied to audit_logs_archive, never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.services.crud import AuditService  # noqa: E402


async def cmd_verify() -> int:
    async with AsyncSessionLocal() as db:
        r = await AuditService.verify_integrity(db)
    print(f"  entries   : {r['total']}")
    print(f"  verified  : {r['checked']}")
    print(f"  valid     : {r['valid']}")
    if not r["valid"]:
        print(f"  broken_at : #{r['broken_at']}  ({r['break_reason']})")
        print(f"  detail    : {r['message']}")
        return 1
    print(f"  {r['message']}")
    return 0


async def cmd_forks() -> int:
    """Rows sharing a previous_hash — the signature of a concurrent-append race."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT previous_hash, count(*) AS n, array_agg(id ORDER BY id) AS ids "
            "FROM audit_logs WHERE previous_hash IS NOT NULL "
            "GROUP BY previous_hash HAVING count(*) > 1 ORDER BY min(id)"
        ))).all()
    if not rows:
        print("  no forks — every entry has a unique predecessor")
        return 0
    print(f"  {len(rows)} fork(s) found:")
    for prev, n, ids in rows:
        print(f"    ids {list(ids)} all chain onto {prev[:16]}… ({n} entries)")
    return 1


async def cmd_reset() -> int:
    async with AsyncSessionLocal() as db:
        total = (await db.execute(select(AuditLog))).scalars().all()
        n = len(total)
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS audit_logs_archive "
            "(LIKE audit_logs INCLUDING DEFAULTS)"
        ))
        # No UNIQUE carried over: the archive holds broken chains by definition.
        await db.execute(text(
            "INSERT INTO audit_logs_archive SELECT * FROM audit_logs"
        ))
        await db.execute(text("DELETE FROM audit_logs"))
        await db.commit()
    print(f"  archived {n} entries to audit_logs_archive, audit_logs is now empty")
    print("  the next logged event becomes the new genesis block")
    return 0


async def cmd_harden() -> int:
    async with AsyncSessionLocal() as db:
        dupes = (await db.execute(text(
            "SELECT count(*) FROM (SELECT previous_hash FROM audit_logs "
            "WHERE previous_hash IS NOT NULL GROUP BY previous_hash "
            "HAVING count(*) > 1) t"
        ))).scalar_one()
        if dupes:
            print(f"  refusing: {dupes} fork(s) present — run --forks, then --reset first")
            return 1
        await db.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_logs_previous_hash "
            "ON audit_logs (previous_hash)"
        ))
        await db.commit()
    print("  UNIQUE index on previous_hash created")
    print("  a forked append now fails with IntegrityError instead of corrupting the chain")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit hash-chain maintenance")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true", help="verify linkage + content")
    g.add_argument("--forks", action="store_true", help="list concurrent-append races")
    g.add_argument("--reset", action="store_true", help="archive chain, start fresh")
    g.add_argument("--harden", action="store_true", help="add UNIQUE index on previous_hash")
    a = ap.parse_args()

    if a.verify:
        return asyncio.run(cmd_verify())
    if a.forks:
        return asyncio.run(cmd_forks())
    if a.reset:
        return asyncio.run(cmd_reset())
    return asyncio.run(cmd_harden())


if __name__ == "__main__":
    raise SystemExit(main())
