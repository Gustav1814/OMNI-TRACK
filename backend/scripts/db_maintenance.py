"""
Manual database maintenance CLI.

Usage (from backend/):
  python -m scripts.db_maintenance migrate
  python -m scripts.db_maintenance analyze
  python -m scripts.db_maintenance retention
  python -m scripts.db_maintenance partitions
  python -m scripts.db_maintenance health
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="OmniTrack database maintenance")
    parser.add_argument(
        "action",
        choices=("migrate", "analyze", "retention", "partitions", "health", "ready"),
        help="Maintenance action to run",
    )
    args = parser.parse_args()

    if args.action == "migrate":
        from app.db.maintenance import run_migrations

        run_migrations()
        print("Migrations applied.")
        return 0

    async def _run() -> int:
        from app.db.maintenance import (
            apply_retention_policy,
            ensure_database_ready,
            get_database_health,
            run_post_migration_maintenance,
            ensure_future_partitions,
        )

        if args.action == "analyze":
            ok = await run_post_migration_maintenance()
            print("ANALYZE completed." if ok else "ANALYZE failed.")
            return 0 if ok else 1
        if args.action == "retention":
            deleted = await apply_retention_policy()
            print(f"Retention purge: {deleted}")
            return 0
        if args.action == "partitions":
            created = await ensure_future_partitions()
            print(f"Partition maintenance: {created}")
            return 0
        if args.action == "health":
            health = await get_database_health()
            for key, val in health.items():
                print(f"{key}: {val}")
            return 0 if health.get("status") == "healthy" else 1
        if args.action == "ready":
            info = await ensure_database_ready()
            print(f"Database ready: {info}")
            return 0
        return 1

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
