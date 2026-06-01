# OmniTrack Database Enterprise Base

This is the database baseline for low-cost, high-throughput deployments.

## Current Strategy

- PostgreSQL 16 with pgvector is the durable store.
- Hot write tables are partition-ready by month:
  - `detections`
  - `embeddings`
  - `foot_traffic`
  - `demographic_snapshots`
- Every hot table has a default partition so inserts continue even if scheduled partition maintenance is missed.
- Startup can create current and future monthly partitions with `DB_CREATE_FUTURE_PARTITIONS_ON_STARTUP=true`.
- Retention first drops expired partitions, then falls back to row deletes for older prototype schemas.
- Re-ID search uses an HNSW pgvector index, while the live gallery stays in memory for low-latency matching.
- Audit logs use an advisory transaction lock so concurrent writers do not fork the SHA-256 chain.

## Cost Controls

- Keep backend DB pools small. Defaults are `DB_POOL_SIZE=8` and `DB_MAX_OVERFLOW=4`.
- Add PgBouncer before increasing API replicas significantly.
- Store raw footage, exports, and model weights outside the database.
- Use monthly partition drops for old camera data instead of massive deletes.
- Write high-volume analytics as aggregates. Avoid per-frame derived metric rows unless the feature truly needs them.

## Operational Commands

From `backend/`:

```bash
python -m scripts.db_maintenance migrate
python -m scripts.db_maintenance partitions
python -m scripts.db_maintenance retention
python -m scripts.db_maintenance analyze
python -m scripts.db_maintenance health
```

## Production Checklist

- Set strong `JWT_SECRET_KEY` and `AES_SECRET_KEY`.
- Turn on PostgreSQL backups and point-in-time recovery at the managed database layer.
- Run `partitions` daily or weekly if app startup is not frequent.
- Monitor:
  - DB pool checked-out connections
  - partition count
  - detection insert latency
  - embedding insert latency
  - HNSW index presence
  - dead tuples and autovacuum lag
- Use read replicas for dashboard-heavy deployments.
- Use a dedicated worker process for AI persistence if camera count grows beyond one backend instance.

## Existing Prototype Databases

The initial schema is optimized for fresh enterprise databases. If an old prototype database already has non-partitioned hot tables, recreate it before production data or write a one-time conversion migration. Do not convert a live high-volume database casually; partition conversion needs a controlled backfill window.
