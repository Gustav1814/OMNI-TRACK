"""Re-ID vector search latency on pgvector with 100k embeddings.

Proposal criterion: appearance search p95 < 100 ms over 100k stored embeddings.

Method
  * A session-private TEMP table (dropped automatically at disconnect, and
    explicitly at the end) receives 100,000 L2-normalised 512-d embeddings;
    nothing touches the app's tables.
  * The embeddings are synthetic but structured like Re-ID data: --identities
    centres with --views noisy views each (pure uniform noise is a pathological
    worst case for any ANN index and does not resemble real features).
  * Same index as the app (alembic 001: HNSW vector_cosine_ops, pgvector
    defaults m=16, ef_construction=64) and the same query shape as
    app.services.crud (ORDER BY vector <=> q LIMIT k).
  * --queries top-10 searches with fresh noisy views; wall-clock latency per
    query measured client-side (includes the network round trip to Postgres).
    recall@10 is checked against exact cosine search in numpy.

    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/search_latency.py
"""

from __future__ import annotations

import asyncio
import time

import numpy as np

from common import base_parser, log, ml_lock, write_result

DIM = 512


def synth(n_ids: int, views: int, noise: float, rng: np.random.Generator):
    centres = rng.standard_normal((n_ids, DIM)).astype(np.float32)
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    x = np.repeat(centres, views, axis=0) + noise * rng.standard_normal((n_ids * views, DIM)).astype(np.float32) / np.sqrt(DIM)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    return centres, x


async def run(args) -> dict:
    import asyncpg  # noqa: PLC0415
    from pgvector.asyncpg import register_vector  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415

    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    rng = np.random.default_rng(args.seed)
    centres, data = synth(args.n // args.views, args.views, args.noise, rng)
    q_ids = rng.integers(0, len(centres), args.queries)
    queries = centres[q_ids] + args.noise * rng.standard_normal((args.queries, DIM)).astype(np.float32) / np.sqrt(DIM)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)

    conn = await asyncpg.connect(dsn)
    try:
        await register_vector(conn)
        pg_version = await conn.fetchval("SHOW server_version")
        vec_version = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
        await conn.execute(f"CREATE TEMP TABLE eval_search_latency (id int PRIMARY KEY, vector vector({DIM}))")
        t = time.perf_counter()
        await conn.copy_records_to_table("eval_search_latency", records=((i, v) for i, v in enumerate(data)),
                                         columns=["id", "vector"])
        load_s = time.perf_counter() - t
        log(f"loaded {len(data)} rows in {load_s:.1f}s; building HNSW")
        await conn.execute(f"SET maintenance_work_mem = '{args.maintenance_mem}'")
        t = time.perf_counter()
        await conn.execute("CREATE INDEX ON eval_search_latency USING hnsw (vector vector_cosine_ops)")
        build_s = time.perf_counter() - t
        await conn.execute("ANALYZE eval_search_latency")
        ef = await conn.fetchval("SHOW hnsw.ef_search")
        log(f"index built in {build_s:.1f}s (ef_search={ef})")
        sql = "SELECT id, vector <=> $1 AS distance FROM eval_search_latency ORDER BY vector <=> $1 LIMIT $2"
        plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + sql, queries[0], args.k))
        for qv in queries[:20]:  # warm-up (not timed)
            await conn.fetch(sql, qv, args.k)
        lat, got = [], []
        for qv in queries:
            t = time.perf_counter()
            rows = await conn.fetch(sql, qv, args.k)
            lat.append((time.perf_counter() - t) * 1000)
            got.append([r["id"] for r in rows])
        await conn.execute("DROP TABLE eval_search_latency")
    finally:
        await conn.close()

    exact = np.argsort(-(queries @ data.T), axis=1)[:, :args.k]
    recall = float(np.mean([len(set(g) & set(e)) / args.k for g, e in zip(got, exact)]))
    lat = np.asarray(lat)
    return {
        "p50_ms": round(float(np.percentile(lat, 50)), 2),
        "p95_ms": round(float(np.percentile(lat, 95)), 2),
        "p99_ms": round(float(np.percentile(lat, 99)), 2),
        "mean_ms": round(float(lat.mean()), 2),
        "recall_at_10_vs_exact": round(recall, 4),
        "n_vectors": int(len(data)),
        "n_queries": int(args.queries),
        "k": args.k,
        "dim": DIM,
        "index": "hnsw vector_cosine_ops (m=16, ef_construction=64)",
        "ef_search": int(ef),
        "load_s": round(load_s, 1),
        "index_build_s": round(build_s, 1),
        "uses_index": "hnsw" in plan.lower() or "Index Scan" in plan,
        "postgres": pg_version,
        "pgvector": vec_version,
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--n", type=int, default=100_000)
    p.add_argument("--views", type=int, default=10, help="views per synthetic identity")
    p.add_argument("--noise", type=float, default=0.6, help="per-view noise (relative to unit centre)")
    p.add_argument("--queries", type=int, default=1000)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--maintenance-mem", default="512MB")
    args = p.parse_args()
    started = time.time()
    with ml_lock():
        m = asyncio.run(run(args))
    log(str(m))
    write_result(
        "search_latency",
        value=m["p95_ms"],
        metrics=m,
        dataset={"name": f"synthetic Re-ID-like embeddings ({args.n // args.views} identities x {args.views} views, "
                         f"noise {args.noise}, seed {args.seed})",
                 "split": f"{args.n} stored, {args.queries} queries", "source": "generated (numpy default_rng)",
                 "n_items": args.n},
        model={"name": "pgvector HNSW", "pgvector": m["pgvector"], "postgres": m["postgres"]},
        device="cpu",
        started=started,
        notes=[
            "Latency is client-side wall clock through asyncpg, so it includes the round trip to Postgres "
            "(Docker Desktop on the same host).",
            "TEMP table only; the app's embeddings tables are not read or written.",
        ],
    )


if __name__ == "__main__":
    main()
