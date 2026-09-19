"""
OmniTrack AI — Footfall
═══════════════════════

Everything here is read from Postgres, so the page survives a restart and shows
history rather than only whatever the pipeline happens to hold in memory.

Three sources, each answering a different question:

  detections           who was present    — distinct track ids per zone, over time
  line_passing_counts  who went through   — directional crossings per line
  roi_dwell            who lingered       — time spent per region

Deliberately NOT used:

  foot_traffic    its direction_in / direction_out / avg_dwell_time columns are
                  never written (the persistence callback only sets person_count),
                  so three of its four useful fields are always zero.
  density /m²     needs a zone's floor area, which nothing collects. The old
                  endpoint invented it as person_count / 50.

Counting rule: a person is a distinct `track_id`, not a detection row. One
person standing still for a minute produces hundreds of detections but is one
visitor. Rows with a NULL track_id cannot be attributed to anyone, so they are
counted separately as `untracked` rather than silently inflating or vanishing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/footfall", tags=["Footfall"])

PERSON_CLASS = "person"
_BUCKETS = {"minute": "minute", "hour": "hour", "day": "day"}


def _window(camera_id, zone, start, end, prefix="") -> tuple[str, Dict[str, Any]]:
    """Shared WHERE fragment + binds. `prefix` qualifies the column names."""
    clauses, binds = [], {}
    p = f"{prefix}." if prefix else ""
    if camera_id is not None:
        clauses.append(f"{p}camera_id = :camera_id")
        binds["camera_id"] = camera_id
    if zone:
        clauses.append(f"{p}zone = :zone")
        binds["zone"] = zone
    if start is not None:
        clauses.append(f"{p}timestamp >= :start")
        binds["start"] = _utc(start)
    if end is not None:
        clauses.append(f"{p}timestamp < :end")
        binds["end"] = _utc(end)
    return (" AND " + " AND ".join(clauses) if clauses else ""), binds


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@router.get("/facets", summary="Filter options present in the data")
async def facets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(text(
        "SELECT DISTINCT camera_id, zone FROM detections "
        "WHERE class_name = :cls AND zone IS NOT NULL ORDER BY camera_id, zone"
    ), {"cls": PERSON_CLASS})).all()

    span = (await db.execute(text(
        "SELECT min(timestamp), max(timestamp) FROM detections WHERE class_name = :cls"
    ), {"cls": PERSON_CLASS})).first()

    # Crossing lines are not restricted to people — right now every crossing on
    # record is a vehicle — so their classes are listed separately.
    classes = (await db.execute(text(
        "SELECT DISTINCT class_name FROM line_passing_counts "
        "WHERE class_name IS NOT NULL ORDER BY class_name"
    ))).scalars().all()

    return {
        "cameras": sorted({int(r[0]) for r in rows if r[0] is not None}),
        "zones": sorted({str(r[1]) for r in rows if r[1]}),
        "crossing_classes": [str(c) for c in classes],
        "earliest": span[0].isoformat() if span and span[0] else None,
        "latest": span[1].isoformat() if span and span[1] else None,
    }


@router.get("/overview", summary="Footfall for a time window")
async def overview(
    camera_id: Optional[int] = None,
    zone: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    bucket: str = Query("hour", pattern="^(minute|hour|day)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    One call for the whole page: headline numbers, a timeline, a per-zone
    breakdown and the crossing totals.
    """
    if bucket not in _BUCKETS:
        raise HTTPException(400, "bucket must be minute, hour or day")
    where, binds = _window(camera_id, zone, start, end)
    binds["cls"] = PERSON_CLASS

    # ── headline ──────────────────────────────────────────────
    totals = (await db.execute(text(
        f"""
        SELECT count(DISTINCT track_id) FILTER (WHERE track_id IS NOT NULL) AS people,
               count(*) FILTER (WHERE track_id IS NULL)                     AS untracked,
               count(*)                                                     AS detections,
               count(DISTINCT zone)                                         AS zones,
               count(DISTINCT camera_id)                                    AS cameras,
               min(timestamp) AS first_seen,
               max(timestamp) AS last_seen
        FROM detections WHERE class_name = :cls {where}
        """
    ), binds)).mappings().first()

    # ── timeline ──────────────────────────────────────────────
    timeline = (await db.execute(text(
        f"""
        SELECT date_trunc('{_BUCKETS[bucket]}', timestamp) AS at,
               count(DISTINCT track_id) FILTER (WHERE track_id IS NOT NULL) AS people,
               count(*) AS detections
        FROM detections WHERE class_name = :cls {where}
        GROUP BY 1 ORDER BY 1
        """
    ), binds)).mappings().all()

    # ── per zone ──────────────────────────────────────────────
    zones = (await db.execute(text(
        f"""
        SELECT camera_id, zone,
               count(DISTINCT track_id) FILTER (WHERE track_id IS NOT NULL) AS people,
               count(*) AS detections,
               min(timestamp) AS first_seen,
               max(timestamp) AS last_seen
        FROM detections WHERE class_name = :cls {where}
        GROUP BY 1, 2 ORDER BY people DESC, detections DESC
        """
    ), binds)).mappings().all()

    # ── dwell, from the latest row per (track, region) ────────
    # roi_dwell counters are cumulative and rewritten every flush, so summing
    # raw rows would multiply every dwell by the number of flushes it survived.
    dwell_where, dwell_binds = _window(camera_id, None, None, None)
    dwell = (await db.execute(text(
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (track_id, region_name)
                   camera_id, region_name, class_name, total_dwell_us
            FROM roi_dwell
            WHERE region_name IS NOT NULL AND region_name <> 'global' {dwell_where}
            ORDER BY track_id, region_name, at_us DESC
        )
        SELECT camera_id, region_name,
               count(*) AS tracks,
               round(avg(total_dwell_us) / 1000000.0, 1) AS avg_dwell_s,
               round(max(total_dwell_us) / 1000000.0, 1) AS max_dwell_s
        FROM latest GROUP BY 1, 2 ORDER BY avg_dwell_s DESC NULLS LAST
        """
    ), dwell_binds)).mappings().all()

    # ── crossings, same cumulative-counter rule ───────────────
    cross_where, cross_binds = _window(camera_id, None, None, None)
    crossings = (await db.execute(text(
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (track_id, region_name)
                   camera_id, region_name, class_name,
                   in_count, out_count, left_count, right_count
            FROM line_passing_counts
            WHERE region_name IS NOT NULL AND region_name <> 'global' {cross_where}
            ORDER BY track_id, region_name, at_us DESC
        )
        SELECT camera_id, region_name, class_name,
               sum(in_count) AS in_count, sum(out_count) AS out_count,
               sum(left_count) AS left_count, sum(right_count) AS right_count,
               count(*) AS tracks
        FROM latest
        GROUP BY 1, 2, 3
        HAVING sum(in_count) + sum(out_count) + sum(left_count) + sum(right_count) > 0
        ORDER BY in_count DESC
        """
    ), cross_binds)).mappings().all()

    def iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    peak = max(timeline, key=lambda r: r["people"]) if timeline else None
    busiest = zones[0] if zones else None

    return {
        "totals": {
            "people": int(totals["people"] or 0),
            "untracked_detections": int(totals["untracked"] or 0),
            "detections": int(totals["detections"] or 0),
            "zones": int(totals["zones"] or 0),
            "cameras": int(totals["cameras"] or 0),
            "first_seen": iso(totals["first_seen"]),
            "last_seen": iso(totals["last_seen"]),
            "peak_bucket": iso(peak["at"]) if peak else None,
            "peak_people": int(peak["people"]) if peak else 0,
            "busiest_zone": busiest["zone"] if busiest else None,
            "busiest_zone_people": int(busiest["people"]) if busiest else 0,
        },
        "bucket": bucket,
        "timeline": [
            {"at": iso(r["at"]), "people": int(r["people"]), "detections": int(r["detections"])}
            for r in timeline
        ],
        "zones": [
            {
                "camera_id": int(r["camera_id"]) if r["camera_id"] is not None else None,
                "zone": r["zone"],
                "people": int(r["people"]),
                "detections": int(r["detections"]),
                "first_seen": iso(r["first_seen"]),
                "last_seen": iso(r["last_seen"]),
            }
            for r in zones
        ],
        "dwell": [
            {
                "camera_id": int(r["camera_id"]) if r["camera_id"] is not None else None,
                "region_name": r["region_name"],
                "tracks": int(r["tracks"]),
                "avg_dwell_s": float(r["avg_dwell_s"] or 0),
                "max_dwell_s": float(r["max_dwell_s"] or 0),
            }
            for r in dwell
        ],
        "crossings": [
            {
                "camera_id": int(r["camera_id"]) if r["camera_id"] is not None else None,
                "region_name": r["region_name"],
                "class_name": r["class_name"],
                "in_count": int(r["in_count"] or 0),
                "out_count": int(r["out_count"] or 0),
                "left_count": int(r["left_count"] or 0),
                "right_count": int(r["right_count"] or 0),
                "tracks": int(r["tracks"]),
            }
            for r in crossings
        ],
    }
