"""
Exclude the pre-HMAC legacy prefix of the audit log from verification.

Rows written before migration 004_security were hashed with *unkeyed* SHA-256,
so anyone with DB write access could always rewrite them, and rows whose
``user_id`` was nulled by the old ``ON DELETE SET NULL`` FK no longer match
their stored hash. They therefore make the whole chain report "broken at #1"
without providing any tamper evidence.

This tool:
  1. verifies that every row before the first HMAC row is legacy (keyed rows can
     never be excluded) and that the keyed part verifies from there;
  2. exports the legacy rows to ``storage/security/audit_legacy_archive_<ts>.json``
     (kept for reference, unverifiable by construction);
  3. signs ``base_id`` / ``base_prev_hash`` into the anchor so the verifier
     starts at the first keyed row;
  4. writes an ``AUDIT_REBASE`` entry to the chain.

The rows stay in the database. Usage (backend/):
    .venv/bin/python -m app.security.audit_rebase            # dry run
    .venv/bin/python -m app.security.audit_rebase --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.security import audit_chain
from app.services.crud import AuditService


async def run(apply: bool) -> int:
    key = audit_chain.audit_key_bytes()
    anchor_path = Path(settings.AUDIT_ANCHOR_PATH)
    async with AsyncSessionLocal() as db:
        entries = await AuditService.load_chain(db)
        current = audit_chain.verify_entries(entries, key, audit_chain.read_anchor(anchor_path, key))
        if current["valid"]:
            print(f"Chain already verifies: {current.get('hmac_entries')} keyed rows, "
                  f"{current.get('excluded_legacy', 0)} excluded. Nothing to do.")
            return 0
        base = audit_chain.legacy_prefix_base(entries)
        if base is None:
            print("No pure legacy prefix before the first keyed row; refusing (this break is NOT legacy history):")
            print(f"  {current['message'] if 'message' in current else current['reason']}")
            return 2
        keyed = [e for e in entries if e.id >= base["base_id"]]
        probe = audit_chain.verify_entries(
            keyed, key, {"status": "ok", "base_id": base["base_id"], "base_prev_hash": base["base_prev_hash"],
                         "last_id": keyed[-1].id, "last_hash": keyed[-1].current_hash},
        )
        if not probe["valid"]:
            print(f"Keyed rows themselves fail at #{probe['broken_at']}: {probe['reason']}. Refusing to rebase.")
            return 2
        print(f"Legacy rows to exclude: {base['archived_before']} (ids < {base['base_id']}); "
              f"keyed rows verified: {probe['hmac_entries']}")
        if not apply:
            print("Dry run. Re-run with --apply to archive and sign the base.")
            return 0

        legacy_rows = (await db.execute(
            select(AuditLog).where(AuditLog.id < base["base_id"]).order_by(AuditLog.id)
        )).scalars().all()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = anchor_path.parent / f"audit_legacy_archive_{ts}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(json.dumps([{
            "id": r.id, "event_type": r.event_type, "user_id": r.user_id, "description": r.description,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None, "ip_address": r.ip_address,
            "current_hash": r.current_hash, "previous_hash": r.previous_hash, "hash_alg": r.hash_alg,
        } for r in legacy_rows], indent=2))

        last = keyed[-1]
        audit_chain.set_anchor_base(anchor_path, base, last.id, last.current_hash, key)
        await AuditService.log_event(
            db, "AUDIT_REBASE", None,
            f"Excluded {base['archived_before']} pre-HMAC legacy audit rows (ids < {base['base_id']})",
            {"archive": archive.name, "base_id": base["base_id"]},
        )
        await db.commit()

        after = await AuditService.verify_integrity(db)
        print(f"Archived to {archive}")
        print(f"Verification now: {after['message']}")
        return 0 if after["valid"] else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="archive legacy rows and sign the chain base")
    sys.exit(asyncio.run(run(ap.parse_args().apply)))


if __name__ == "__main__":
    main()
