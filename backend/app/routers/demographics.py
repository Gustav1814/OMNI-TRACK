"""
OmniTrack AI — Audience Mix
═══════════════════════════

Age and gender, read from Postgres so the page shows history and survives a
restart.

What changed, and why it matters:

  This endpoint used to end in a cold-start fallback returning a hand-written
  age curve and a 72/66 gender split — 138 people who did not exist. Nothing
  ever wrote `demographic_snapshots`, so `total_count` was always 0 and that
  fallback was the ONLY path a caller could reach. It is gone. An empty store
  now reports an empty store.

Counting rule: one row is one VISITOR, not one observation. DeepFace's age
estimate moves several years between consecutive frames of the same face, so
the pipeline banks every sample against the person's track and writes a single
row with the median age and the modal gender once the visit ends. Rows with a
NULL track_id are faces that could not be tied to a tracked person; they carry
no age or gender and are reported separately as `unattributed_faces` rather
than silently inflating or vanishing — the same treatment Footfall gives
untracked detections.

Honesty: `min_sample_for_percentages` tells the page when a share is worth
printing. "62% female" out of 8 faces is noise presented as fact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AGE_GROUPS
from app.database import get_db
from app.models.user import User
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/demographics", tags=["Demographics"])

_BUCKETS = {"minute": "minute", "hour": "hour", "day": "day"}

# Below this many analysed visitors a percentage says more about the sample
# than about the store, so the page prints counts only.
MIN_SAMPLE_FOR_PERCENTAGES = 30

# Age is an estimate. DeepFace's published MAE is a few years, and CCTV faces
# are smaller and worse-lit than the benchmark's — hence buckets, not ages.
_AGE_CAVEAT = (
    "Age is estimated from the face and carries an error of several years, "
    "which is why results are grouped into bands rather than shown as exact ages."
)

_VISITORS = "track_id IS NOT NULL"
_UNATTRIBUTED = "track_id IS NULL"


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _where(
    base: str,
    camera_id: Optional[int],
    zone: Optional[str],
    start: Optional[datetime],
    end: Optional[datetime],
) -> tuple[str, Dict[str, Any]]:
    clauses, binds = [base], {}
    if camera_id is not None:
        clauses.append("camera_id = :camera_id")
        binds["camera_id"] = camera_id
    if zone:
        clauses.append("zone = :zone")
        binds["zone"] = zone
    if start is not None:
        clauses.append("timestamp >= :start")
        binds["start"] = _utc(start)
    if end is not None:
        clauses.append("timestamp < :end")
        binds["end"] = _utc(end)
    return " WHERE " + " AND ".join(clauses), binds


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


@router.get("/facets", summary="Filter options present in the data")
async def facets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cameras and zones that actually have rows — never a fixed list, so the
    filter cannot offer a camera that was never run.
    """
    rows = (await db.execute(text(
        "SELECT DISTINCT camera_id, zone FROM demographic_snapshots ORDER BY camera_id"
    ))).all()
    span = (await db.execute(text(
        "SELECT min(timestamp), max(timestamp) FROM demographic_snapshots"
    ))).first()

    return {
        "cameras": sorted({int(r[0]) for r in rows if r[0] is not None}),
        "zones": sorted({r[1] for r in rows if r[1]}),
        "age_groups": list(AGE_GROUPS),
        "earliest": _iso(span[0]) if span else None,
        "latest": _iso(span[1]) if span else None,
        "min_sample_for_percentages": MIN_SAMPLE_FOR_PERCENTAGES,
    }


@router.get("/overview", summary="Audience mix for a window")
async def overview(
    camera_id: Optional[int] = None,
    zone: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    bucket: str = Query("hour", pattern="^(minute|hour|day)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    where, binds = _where(_VISITORS, camera_id, zone, start, end)
    un_where, un_binds = _where(_UNATTRIBUTED, camera_id, zone, start, end)
    trunc = _BUCKETS.get(bucket, "hour")

    # ── totals ────────────────────────────────────────────────
    # Median age, not mean: one bad estimate on a half-turned face drags an
    # average and leaves the median where it was.
    totals = (await db.execute(text(
        f"""
        SELECT count(*) AS visitors,
               coalesce(sum(sample_count), 0) AS faces,
               round((percentile_cont(0.5)
                      WITHIN GROUP (ORDER BY estimated_age))::numeric, 1) AS median_age,
               count(*) FILTER (WHERE age_group IS NOT NULL) AS with_age,
               count(*) FILTER (WHERE gender IN ('male', 'female')) AS with_gender,
               count(DISTINCT zone) AS zones,
               count(DISTINCT camera_id) AS cameras,
               min(timestamp) AS first_at,
               max(timestamp) AS last_at
        FROM demographic_snapshots {where}
        """
    ), binds)).mappings().first()

    unattributed = (await db.execute(text(
        f"SELECT coalesce(sum(sample_count), 0) AS faces FROM demographic_snapshots {un_where}"
    ), un_binds)).scalar() or 0

    # ── age x gender ──────────────────────────────────────────
    # Kept as a JOINT distribution. The old breakdown grouped age and gender
    # separately, which marginalises the cross-tab away and makes a question
    # like "women aged 26-35" unanswerable.
    cross = (await db.execute(text(
        f"""
        SELECT coalesce(age_group, 'unknown') AS age_group,
               coalesce(gender, 'unknown') AS gender,
               count(*) AS n
        FROM demographic_snapshots {where}
        GROUP BY 1, 2
        """
    ), binds)).mappings().all()

    # ── who visits when ───────────────────────────────────────
    timeline_rows = (await db.execute(text(
        f"""
        SELECT date_trunc('{trunc}', timestamp) AS at,
               coalesce(age_group, 'unknown') AS age_group,
               count(*) AS n
        FROM demographic_snapshots {where}
        GROUP BY 1, 2 ORDER BY 1
        """
    ), binds)).mappings().all()

    # ── per zone ──────────────────────────────────────────────
    zones = (await db.execute(text(
        f"""
        SELECT camera_id,
               coalesce(zone, 'default') AS zone,
               count(*) AS visitors,
               coalesce(sum(sample_count), 0) AS faces,
               round((percentile_cont(0.5)
                      WITHIN GROUP (ORDER BY estimated_age))::numeric, 1) AS median_age,
               count(*) FILTER (WHERE gender = 'male') AS male,
               count(*) FILTER (WHERE gender = 'female') AS female,
               count(*) FILTER (WHERE gender IS NULL OR gender NOT IN ('male', 'female'))
                   AS unknown_gender,
               max(timestamp) AS last_at
        FROM demographic_snapshots {where}
        GROUP BY 1, 2 ORDER BY visitors DESC
        """
    ), binds)).mappings().all()

    # ── mood ──────────────────────────────────────────────────
    # Emotion is recorded on the same row because it comes from the same
    # DeepFace call. It is the only persisted emotion history in the system.
    moods = (await db.execute(text(
        f"""
        SELECT dominant_emotion AS emotion, count(*) AS n
        FROM demographic_snapshots {where} AND dominant_emotion IS NOT NULL
        GROUP BY 1 ORDER BY n DESC
        """
    ), binds)).mappings().all()

    # ── shape the cross-tab ───────────────────────────────────
    order = list(AGE_GROUPS) + ["unknown"]
    by_age: Dict[str, Dict[str, int]] = {}
    gender_totals: Dict[str, int] = {}
    for r in cross:
        slot = by_age.setdefault(r["age_group"], {})
        slot[r["gender"]] = slot.get(r["gender"], 0) + int(r["n"])
        gender_totals[r["gender"]] = gender_totals.get(r["gender"], 0) + int(r["n"])

    pyramid = [
        {
            "age_group": group,
            "male": by_age.get(group, {}).get("male", 0),
            "female": by_age.get(group, {}).get("female", 0),
            "unknown": by_age.get(group, {}).get("unknown", 0),
            "total": sum(by_age.get(group, {}).values()),
        }
        for group in order
        if by_age.get(group)
    ]

    timeline: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}
    for r in timeline_rows:
        key = r["at"].isoformat()
        entry = index.get(key)
        if entry is None:
            entry = {"at": key, "total": 0}
            index[key] = entry
            timeline.append(entry)
        entry[r["age_group"]] = entry.get(r["age_group"], 0) + int(r["n"])
        entry["total"] += int(r["n"])

    visitors = int(totals["visitors"] or 0)

    return {
        "bucket": bucket,
        "age_groups": list(AGE_GROUPS),
        "min_sample_for_percentages": MIN_SAMPLE_FOR_PERCENTAGES,
        "age_caveat": _AGE_CAVEAT,
        "totals": {
            "visitors": visitors,
            "faces_sampled": int(totals["faces"] or 0),
            "unattributed_faces": int(unattributed),
            "median_age": float(totals["median_age"]) if totals["median_age"] is not None else None,
            "with_age": int(totals["with_age"] or 0),
            "with_gender": int(totals["with_gender"] or 0),
            "zones": int(totals["zones"] or 0),
            "cameras": int(totals["cameras"] or 0),
            "first_seen": _iso(totals["first_at"]),
            "last_seen": _iso(totals["last_at"]),
            # Enough of a sample for shares to mean anything?
            "reportable": visitors >= MIN_SAMPLE_FOR_PERCENTAGES,
        },
        "gender_distribution": gender_totals,
        "age_distribution": {
            group: sum(by_age.get(group, {}).values())
            for group in order if by_age.get(group)
        },
        "pyramid": pyramid,
        "timeline": timeline,
        "zones": [
            {
                "camera_id": int(r["camera_id"]),
                "zone": r["zone"],
                "visitors": int(r["visitors"]),
                "faces_sampled": int(r["faces"] or 0),
                "median_age": float(r["median_age"]) if r["median_age"] is not None else None,
                "male": int(r["male"] or 0),
                "female": int(r["female"] or 0),
                "unknown_gender": int(r["unknown_gender"] or 0),
                "last_seen": _iso(r["last_at"]),
            }
            for r in zones
        ],
        "moods": [
            {"emotion": r["emotion"], "count": int(r["n"])}
            for r in moods
        ],
    }


@router.get("/current", summary="Age/gender totals (compatibility)")
async def current(
    zone: Optional[str] = None,
    hours: int = 24,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The original shape, kept for anything still calling it — but reporting the
    real table. The hand-written 138-person fallback that used to live here is
    gone; no visitors now returns no visitors.
    """
    from datetime import timedelta

    start = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    where, binds = _where(_VISITORS, None, zone, start, None)

    rows = (await db.execute(text(
        f"""
        SELECT coalesce(age_group, 'unknown') AS age_group,
               coalesce(gender, 'unknown') AS gender,
               count(*) AS n
        FROM demographic_snapshots {where}
        GROUP BY 1, 2
        """
    ), binds)).mappings().all()

    age_dist: Dict[str, int] = {}
    gender_dist: Dict[str, int] = {}
    total = 0
    for r in rows:
        n = int(r["n"])
        total += n
        if r["age_group"] != "unknown":
            age_dist[r["age_group"]] = age_dist.get(r["age_group"], 0) + n
        gender_dist[r["gender"]] = gender_dist.get(r["gender"], 0) + n

    return {
        "zone": zone,
        "age_distribution": age_dist,
        "gender_distribution": gender_dist,
        "total_count": total,
    }
