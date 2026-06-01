"""
OmniTrack AI — Database Maintenance (Enterprise)

- Alembic migrations on startup (production-safe schema)
- pgvector extension + HNSW index (via migrations)
- ANALYZE for query planner statistics
- Configurable retention for high-volume tables
- Health metrics for /api/health
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from loguru import logger
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.models.analytics import (
    CustomerJourney,
    DemographicSnapshot,
    FootTraffic,
    PeakHoursData,
    StoreVibeScore,
)
from app.models.detection import Detection
from app.models.embedding import Embedding

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ANALYTICS_TABLES = (
    "foot_traffic",
    "customer_journeys",
    "demographic_snapshots",
    "store_vibe_scores",
    "peak_hours",
)

_PARTITIONED_TABLES = {
    "detections": "timestamp",
    "embeddings": "timestamp",
    "foot_traffic": "timestamp",
    "demographic_snapshots": "timestamp",
}


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    return cfg


def run_migrations() -> str:
    """Apply pending Alembic migrations synchronously (uses async env.py)."""
    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head() or "none"
    logger.info(f"Running Alembic migrations → head ({head})")
    command.upgrade(cfg, "head")
    return head


def get_migration_revision() -> Optional[str]:
    """Return applied Alembic revision from alembic_version, if any."""
    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


async def _fetch_scalar(conn, sql: str) -> Any:
    result = await conn.execute(text(sql))
    return result.scalar()


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month)


def _partition_name(table: str, start: datetime) -> str:
    return f"{table}_y{start.year:04d}m{start.month:02d}"


async def _is_partitioned(conn, table: str) -> bool:
    return bool(
        await _fetch_scalar(
            conn,
            (
                "SELECT EXISTS("
                "SELECT 1 FROM pg_class WHERE relname = "
                f"'{table}' AND relkind = 'p')"
            ),
        )
    )


async def ensure_future_partitions(months_ahead: int | None = None) -> Dict[str, int]:
    """
    Create monthly range partitions for high-volume tables.

    If a table is not partitioned, this is a no-op so older prototype schemas
    still start. Fresh enterprise schemas get cheap partition-drop retention.
    """
    if not settings.DATABASE_URL.startswith("postgresql"):
        return {}

    created: Dict[str, int] = {table: 0 for table in _PARTITIONED_TABLES}
    months = max(0, months_ahead if months_ahead is not None else settings.DB_PARTITION_MONTHS_AHEAD)
    current = _month_start(datetime.now(timezone.utc))

    async with engine.begin() as conn:
        for table, column in _PARTITIONED_TABLES.items():
            if not await _is_partitioned(conn, table):
                continue
            for offset in range(months + 1):
                start = _add_months(current, offset)
                end = _add_months(start, 1)
                name = _partition_name(table, start)
                await conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {name} "
                        f"PARTITION OF {table} "
                        f"FOR VALUES FROM ('{start.isoformat()}') "
                        f"TO ('{end.isoformat()}')"
                    )
                )
                if table == "embeddings":
                    await conn.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS {name}_vector_hnsw "
                            f"ON {name} USING hnsw (vector vector_cosine_ops)"
                        )
                    )
                created[table] += 1

    logger.info(f"Partition maintenance completed: {created}")
    return created


async def drop_expired_partitions(cutoffs: Dict[str, datetime]) -> Dict[str, int]:
    """
    Drop whole monthly partitions older than cutoff. This is dramatically
    cheaper than deleting millions of rows from hot tables.
    """
    dropped: Dict[str, int] = {}
    if not settings.DATABASE_URL.startswith("postgresql"):
        return dropped

    async with engine.begin() as conn:
        for table, cutoff in cutoffs.items():
            if table not in _PARTITIONED_TABLES or not await _is_partitioned(conn, table):
                continue
            rows = await conn.execute(
                text(
                    """
                    SELECT c.relname
                    FROM pg_inherits i
                    JOIN pg_class c ON c.oid = i.inhrelid
                    JOIN pg_class p ON p.oid = i.inhparent
                    WHERE p.relname = :table
                      AND c.relname ~ :pattern
                    """
                ),
                {"table": table, "pattern": f"^{table}_y[0-9]{{4}}m[0-9]{{2}}$"},
            )
            dropped[table] = 0
            for (name,) in rows.all():
                try:
                    year = int(name.rsplit("_y", 1)[1][:4])
                    month = int(name.rsplit("m", 1)[1])
                    partition_start = datetime(year, month, 1, tzinfo=timezone.utc)
                    partition_end = _add_months(partition_start, 1)
                except Exception:
                    continue
                if partition_end < cutoff:
                    await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
                    dropped[table] += 1

    return dropped


async def wait_for_database() -> None:
    """Block until PostgreSQL accepts connections or retries are exhausted."""
    if not settings.DATABASE_URL.startswith("postgresql"):
        return

    last_err: Exception | None = None
    for attempt in range(1, settings.DB_STARTUP_MAX_RETRIES + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✅ PostgreSQL is accepting connections")
            return
        except Exception as e:
            last_err = e
            if attempt < settings.DB_STARTUP_MAX_RETRIES:
                logger.info(
                    f"Waiting for PostgreSQL ({attempt}/{settings.DB_STARTUP_MAX_RETRIES})..."
                )
                await asyncio.sleep(settings.DB_STARTUP_RETRY_SECONDS)

    hint = (
        "Start infrastructure: docker compose up -d postgres redis "
        "(from repo root)"
    )
    raise ConnectionError(
        f"PostgreSQL not available after {settings.DB_STARTUP_MAX_RETRIES} attempts: "
        f"{last_err}. {hint}"
    )


async def _fetch_revision_from_db() -> Optional[str]:
    try:
        async with engine.connect() as conn:
            return await _fetch_scalar(
                conn,
                "SELECT version_num FROM alembic_version LIMIT 1",
            )
    except Exception:
        return None


async def ensure_database_ready() -> Dict[str, Any]:
    """
    Production startup: migrations, extension check, optional ANALYZE + retention.
    Falls back to create_all when migrations are disabled (local dev).
    """
    from app.database import Base

    info: Dict[str, Any] = {
        "migrations_applied": False,
        "revision": None,
        "pgvector": False,
        "analyzed": False,
        "retention": {},
    }

    if not settings.DATABASE_URL.startswith("postgresql"):
        logger.warning("Non-PostgreSQL URL — using create_all (dev only)")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        info["migrations_applied"] = True
        return info

    await wait_for_database()

    if settings.DB_RUN_MIGRATIONS_ON_STARTUP:
        head = await asyncio.to_thread(run_migrations)
        info["migrations_applied"] = True
        info["revision"] = head
    else:
        logger.info("DB_RUN_MIGRATIONS_ON_STARTUP=false — verifying schema only")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        info["migrations_applied"] = False
        rev = await _fetch_revision_from_db()
        info["revision"] = rev

    async with engine.connect() as conn:
        ext = await _fetch_scalar(
            conn,
            "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')",
        )
        info["pgvector"] = bool(ext)

    if settings.DB_RUN_ANALYZE_ON_STARTUP:
        info["analyzed"] = await run_post_migration_maintenance()

    if settings.DB_CREATE_FUTURE_PARTITIONS_ON_STARTUP:
        info["partitions"] = await ensure_future_partitions()

    if settings.DB_RETENTION_ON_STARTUP:
        info["retention"] = await apply_retention_policy()

    return info


async def run_post_migration_maintenance() -> bool:
    """Refresh planner statistics on hot tables (safe, non-blocking ANALYZE)."""
    try:
        async with engine.begin() as conn:
            tables = [
                "users",
                "cameras",
                "detections",
                "embeddings",
                "audit_logs",
                *_ANALYTICS_TABLES,
            ]
            for table in tables:
                await conn.execute(text(f"ANALYZE {table}"))
        logger.info("✅ PostgreSQL ANALYZE completed on core tables")
        return True
    except Exception as e:
        logger.warning(f"ANALYZE skipped: {e}")
        return False


async def apply_retention_policy() -> Dict[str, int]:
    """
    Purge aged rows from high-volume tables. Audit logs are never deleted.
    Returns counts deleted per table.
    """
    deleted: Dict[str, int] = {}
    now = datetime.now(timezone.utc)
    cutoffs: Dict[str, datetime] = {}
    if settings.DETECTION_RETENTION_DAYS > 0:
        cutoffs["detections"] = now - timedelta(days=settings.DETECTION_RETENTION_DAYS)
    if settings.EMBEDDING_RETENTION_DAYS > 0:
        cutoffs["embeddings"] = now - timedelta(days=settings.EMBEDDING_RETENTION_DAYS)
    if settings.ANALYTICS_RETENTION_DAYS > 0:
        analytics_cutoff = now - timedelta(days=settings.ANALYTICS_RETENTION_DAYS)
        cutoffs["foot_traffic"] = analytics_cutoff
        cutoffs["demographic_snapshots"] = analytics_cutoff

    dropped = await drop_expired_partitions(cutoffs)
    if dropped:
        deleted.update({f"{key}_partitions": value for key, value in dropped.items()})

    async with AsyncSessionLocal() as db:
        try:
            if settings.DETECTION_RETENTION_DAYS > 0:
                cutoff = now - timedelta(days=settings.DETECTION_RETENTION_DAYS)
                result = await db.execute(
                    delete(Detection).where(Detection.timestamp < cutoff)
                )
                deleted["detections"] = result.rowcount or 0

            if settings.EMBEDDING_RETENTION_DAYS > 0:
                cutoff = now - timedelta(days=settings.EMBEDDING_RETENTION_DAYS)
                result = await db.execute(
                    delete(Embedding).where(Embedding.timestamp < cutoff)
                )
                deleted["embeddings"] = result.rowcount or 0

            if settings.ANALYTICS_RETENTION_DAYS > 0:
                cutoff = now - timedelta(days=settings.ANALYTICS_RETENTION_DAYS)
                for model, key in (
                    (FootTraffic, "foot_traffic"),
                    (CustomerJourney, "customer_journeys"),
                    (DemographicSnapshot, "demographic_snapshots"),
                    (StoreVibeScore, "store_vibe_scores"),
                    (PeakHoursData, "peak_hours"),
                ):
                    ts_col = getattr(model, "timestamp", None) or getattr(model, "date")
                    result = await db.execute(delete(model).where(ts_col < cutoff))
                    deleted[key] = result.rowcount or 0

            await db.commit()
            if any(deleted.values()):
                logger.info(f"Retention purge: {deleted}")
        except Exception as e:
            await db.rollback()
            logger.warning(f"Retention policy skipped: {e}")
            deleted["error"] = 1

    return deleted


async def get_database_health(db_engine: AsyncEngine = engine) -> Dict[str, Any]:
    """Enterprise health snapshot for monitoring."""
    health: Dict[str, Any] = {
        "status": "unhealthy",
        "dialect": settings.DATABASE_URL.split(":", 1)[0],
        "pool": {},
        "revision": None,
        "pgvector": False,
        "table_stats": {},
    }

    try:
        async with db_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            health["status"] = "healthy"

            pool = db_engine.sync_engine.pool
            health["pool"] = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            }

            if settings.DATABASE_URL.startswith("postgresql"):
                health["pgvector"] = bool(
                    await _fetch_scalar(
                        conn,
                        "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')",
                    )
                )
                rev = await _fetch_scalar(
                    conn,
                    "SELECT version_num FROM alembic_version LIMIT 1",
                )
                health["revision"] = rev

                for table in ("detections", "embeddings", "audit_logs"):
                    count = await _fetch_scalar(
                        conn,
                        f"SELECT reltuples::bigint FROM pg_class WHERE relname = '{table}'",
                    )
                    health["table_stats"][table] = {
                        "estimated_rows": int(count or 0),
                    }

                idx = await _fetch_scalar(
                    conn,
                    "SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE indexname LIKE '%embeddings%vector_hnsw')",
                )
                health["table_stats"]["embeddings"]["hnsw_index"] = bool(idx)

                health["partitions"] = {}
                for table in _PARTITIONED_TABLES:
                    partitioned = await _is_partitioned(conn, table)
                    count = 0
                    if partitioned:
                        count = int(
                            await _fetch_scalar(
                                conn,
                                (
                                    "SELECT count(*) FROM pg_inherits i "
                                    "JOIN pg_class p ON p.oid = i.inhparent "
                                    f"WHERE p.relname = '{table}'"
                                ),
                            )
                            or 0
                        )
                    health["partitions"][table] = {
                        "partitioned": partitioned,
                        "child_count": count,
                    }

    except Exception as e:
        health["error"] = str(e)

    return health
