"""
OmniTrack AI — Queue Insights
═════════════════════════════

History from Postgres, current depth from the pipeline. Splitting the two
matters: the historical view must keep working when nothing is running, and the
old endpoints returned zeros for everything the moment the pipeline was idle.

  GET /api/checkout/facets     cameras, lanes and the span of recorded data
  GET /api/checkout/overview   totals, per-lane figures, queue depth over time
  GET /api/checkout/live       queue right now, from the running pipeline

On naming: `time_in_lane` is the interval between entering and leaving a lane's
box. It is wait AND service together — one region cannot separate them — so it
is not called service time anywhere in this API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/checkout", tags=["Queue Insights"])

_BUCKETS = {"minute": "minute", "hour": "hour", "day": "day"}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _us(dt: datetime) -> int:
    return int(_utc(dt).timestamp() * 1_000_000)


def _filters(camera_id, lane_id, job_id, start, end, ts_col: str):
    clauses, binds = [], {}
    if camera_id is not None:
        clauses.append("camera_id = :camera_id")
        binds["camera_id"] = camera_id
    if lane_id:
        clauses.append("lane_id = :lane_id")
        binds["lane_id"] = lane_id
    if job_id:
        clauses.append("job_id = :job_id")
        binds["job_id"] = job_id
    if start is not None:
        clauses.append(f"{ts_col} >= :start_us")
        binds["start_us"] = _us(start)
    if end is not None:
        clauses.append(f"{ts_col} < :end_us")
        binds["end_us"] = _us(end)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), binds


def _iso_us(v) -> Optional[str]:
    if v is None:
        return None
    return datetime.fromtimestamp(int(v) / 1_000_000, tz=timezone.utc).isoformat()


@router.get("/facets", summary="Filter options present in the data")
async def facets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lanes = (await db.execute(text(
        "SELECT DISTINCT camera_id, lane_id, lane_name FROM checkout_services "
        "UNION "
        "SELECT DISTINCT camera_id, lane_id, lane_name FROM checkout_samples "
        "ORDER BY camera_id, lane_id"
    ))).all()
    span = (await db.execute(text(
        "SELECT min(enter_at_us), max(exit_at_us) FROM checkout_services"
    ))).first()
    jobs = (await db.execute(text(
        "SELECT DISTINCT job_id FROM checkout_services ORDER BY job_id"
    ))).scalars().all()

    return {
        "cameras": sorted({int(r[0]) for r in lanes if r[0] is not None}),
        "lanes": [
            {"camera_id": int(r[0]), "lane_id": r[1], "lane_name": r[2] or r[1]}
            for r in lanes if r[1]
        ],
        "jobs": [str(j) for j in jobs],
        "earliest": _iso_us(span[0]) if span else None,
        "latest": _iso_us(span[1]) if span else None,
    }


@router.get("/overview", summary="Queue history for a window")
async def overview(
    camera_id: Optional[int] = None,
    lane_id: Optional[str] = None,
    job_id: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    bucket: str = Query("minute", pattern="^(minute|hour|day)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if bucket not in _BUCKETS:
        raise HTTPException(400, "bucket must be minute, hour or day")

    svc_where, svc_binds = _filters(camera_id, lane_id, job_id, start, end, "exit_at_us")
    smp_where, smp_binds = _filters(camera_id, lane_id, job_id, start, end, "at_us")
    trunc = _BUCKETS[bucket]

    # ── totals ────────────────────────────────────────────────
    # Median as well as mean: one abandoned trolley sitting in frame for twenty
    # minutes drags an average badly, and the median is what a shopper actually
    # experiences.
    totals = (await db.execute(text(
        f"""
        SELECT count(*) AS served,
               round(avg(time_in_lane_s)::numeric, 1) AS avg_s,
               round((percentile_cont(0.5) WITHIN GROUP (ORDER BY time_in_lane_s))::numeric, 1) AS median_s,
               round(max(time_in_lane_s)::numeric, 1) AS max_s,
               count(DISTINCT lane_id) AS lanes
        FROM checkout_services {svc_where}
        """
    ), svc_binds)).mappings().first()

    peak = (await db.execute(text(
        f"SELECT max(queue_length) AS peak FROM checkout_samples {smp_where}"
    ), smp_binds)).mappings().first()

    # ── per lane ──────────────────────────────────────────────
    lanes = (await db.execute(text(
        f"""
        SELECT camera_id, lane_id, max(lane_name) AS lane_name,
               count(*) AS served,
               round(avg(time_in_lane_s)::numeric, 1) AS avg_s,
               round((percentile_cont(0.5) WITHIN GROUP (ORDER BY time_in_lane_s))::numeric, 1) AS median_s,
               round(max(time_in_lane_s)::numeric, 1) AS max_s,
               min(enter_at_us) AS first_us, max(exit_at_us) AS last_us
        FROM checkout_services {svc_where}
        GROUP BY camera_id, lane_id ORDER BY served DESC
        """
    ), svc_binds)).mappings().all()

    peaks = {
        r["lane_id"]: int(r["peak"] or 0)
        for r in (await db.execute(text(
            f"SELECT lane_id, max(queue_length) AS peak FROM checkout_samples "
            f"{smp_where} GROUP BY lane_id"
        ), smp_binds)).mappings().all()
    }

    # ── queue depth over time ─────────────────────────────────
    # avg across the samples in each bucket, not the sum: two lanes sampled in
    # the same minute describe one moment, and adding them would invent depth.
    timeline = (await db.execute(text(
        f"""
        SELECT date_trunc('{trunc}', to_timestamp(at_us / 1000000.0)) AS at,
               round(avg(queue_length)::numeric, 1) AS avg_queue,
               max(queue_length) AS peak_queue
        FROM checkout_samples {smp_where}
        GROUP BY 1 ORDER BY 1
        """
    ), smp_binds)).mappings().all()

    # ── distribution ──────────────────────────────────────────
    # Buckets show whether a lane is steadily slow or occasionally stuck, which
    # any single number hides.
    dist = (await db.execute(text(
        f"""
        SELECT width_bucket(time_in_lane_s, 0, 300, 10) AS b, count(*) AS n
        FROM checkout_services {svc_where}
        GROUP BY 1 ORDER BY 1
        """
    ), svc_binds)).mappings().all()

    def throughput(served: int, first_us, last_us) -> float:
        if not served or first_us is None or last_us is None:
            return 0.0
        hours = (int(last_us) - int(first_us)) / 1_000_000 / 3600
        return round(served / hours, 1) if hours > 0.0003 else 0.0

    busiest = lanes[0] if lanes else None

    return {
        "bucket": bucket,
        "totals": {
            "served": int(totals["served"] or 0),
            "avg_time_in_lane_s": float(totals["avg_s"] or 0),
            "median_time_in_lane_s": float(totals["median_s"] or 0),
            "max_time_in_lane_s": float(totals["max_s"] or 0),
            "lanes": int(totals["lanes"] or 0),
            "peak_queue": int((peak or {}).get("peak") or 0),
            "busiest_lane": (busiest["lane_name"] or busiest["lane_id"]) if busiest else None,
            "busiest_lane_served": int(busiest["served"]) if busiest else 0,
        },
        "lanes": [
            {
                "camera_id": int(r["camera_id"]),
                "lane_id": r["lane_id"],
                "lane_name": r["lane_name"] or r["lane_id"],
                "served": int(r["served"]),
                "avg_time_in_lane_s": float(r["avg_s"] or 0),
                "median_time_in_lane_s": float(r["median_s"] or 0),
                "max_time_in_lane_s": float(r["max_s"] or 0),
                "peak_queue": peaks.get(r["lane_id"], 0),
                "throughput_per_hour": throughput(r["served"], r["first_us"], r["last_us"]),
                "first_seen": _iso_us(r["first_us"]),
                "last_seen": _iso_us(r["last_us"]),
            }
            for r in lanes
        ],
        "timeline": [
            {
                "at": r["at"].isoformat(),
                "avg_queue": float(r["avg_queue"] or 0),
                "peak_queue": int(r["peak_queue"] or 0),
            }
            for r in timeline
        ],
        "distribution": [
            {
                "from_s": (int(r["b"]) - 1) * 30,
                "to_s": int(r["b"]) * 30,
                "count": int(r["n"]),
            }
            for r in dist if r["b"] is not None
        ],
    }


@router.get("/live", summary="Queue right now")
async def live(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Current depth per lane, straight from the running pipeline.

    Returns an empty list when nothing is running — the historical view is a
    separate endpoint precisely so an idle pipeline cannot blank it.
    """
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None or getattr(pipeline, "checkout", None) is None:
        return {"running": False, "lanes": []}

    lanes: list[Dict[str, Any]] = []
    for cam_id, result in (getattr(pipeline, "_results_buffer", None) or {}).items():
        for lane in (getattr(result, "checkout_data", None) or {}).get("lanes") or []:
            lanes.append({
                "camera_id": cam_id,
                "lane_id": lane.get("lane_id"),
                "lane_name": lane.get("lane_name") or lane.get("lane_id"),
                "queue_length": int(lane.get("queue_length", 0)),
                "avg_time_in_lane_s": float(lane.get("avg_time_in_lane", 0)),
                "wait_estimate_s": float(lane.get("current_wait_estimate", 0)),
                "throughput_per_hour": float(lane.get("throughput", 0)),
                "served": int(lane.get("total_served", 0)),
            })

    return {
        "running": bool(getattr(pipeline, "checkout", None) and pipeline.checkout.lanes),
        "configured_lanes": len(getattr(pipeline.checkout, "lanes", [])),
        "lanes": sorted(lanes, key=lambda l: l["lane_name"]),
    }
