"""
OmniTrack AI — Alerts
═════════════════════

Threshold breaches raised by running jobs (currently ROI dwell overruns), with
the filtering, paging and acknowledgement the Alerts page needs.

Evidence images are resolved rather than stored. `job_alerts.snapshot_path` was
never populated, but the artifact index already knows every crop's camera,
track, region and timestamp — so an alert's images are simply the crops of that
track, in that region, around the moment it fired. Same for the clip: whichever
segment covers that timestamp.

That means evidence works retroactively for alerts raised before any of this
existed, and no second copy of an image is written.

  GET   /api/alerts                filtered + paginated
  GET   /api/alerts/facets         distinct cameras/zones/jobs for the filters
  GET   /api/alerts/{id}/images    crops of the tracked object around the alert
  GET   /api/alerts/{id}/video     the clip covering the alert
  POST  /api/alerts/{id}/ack       acknowledge
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import resolved_artifacts_dir
from app.database import get_db
from app.models.job import JobAlert
from app.models.user import User
from app.security.dependencies import get_current_user
from app.services import artifact_index

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 25

# How far either side of an alert to look for evidence. An alert fires after the
# dwell threshold has already elapsed, so the interesting frames are mostly
# BEFORE it — hence the asymmetry.
_EVIDENCE_BEFORE_S = 60
_EVIDENCE_AFTER_S = 15
_MAX_EVIDENCE_IMAGES = 24


def _at(alert: JobAlert) -> datetime:
    """Alert time as UTC. `at_us` is epoch microseconds."""
    return datetime.fromtimestamp((alert.at_us or 0) / 1_000_000, tz=timezone.utc)


def _serialise(alert: JobAlert) -> Dict[str, Any]:
    return {
        "alert_id": str(alert.id),
        "alert_type": alert.alert_type,
        "job_id": alert.job_id,
        "camera_id": alert.camera_id,
        "zone": alert.zone,
        "region_name": alert.region_name,
        "track_id": alert.track_id,
        "class_name": alert.class_name,
        "value": alert.value,
        "threshold": alert.threshold,
        "message": alert.message,
        "acknowledged": bool(alert.acknowledged),
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "at_ts": _at(alert).isoformat(),
    }


async def _get_alert(db: AsyncSession, alert_id: str) -> JobAlert:
    try:
        pk = int(alert_id)
    except (TypeError, ValueError):
        raise HTTPException(400, "alert_id must be numeric")
    alert = (await db.execute(select(JobAlert).where(JobAlert.id == pk))).scalar_one_or_none()
    if alert is None:
        raise HTTPException(404, f"No alert with id {alert_id}")
    return alert


@router.get("", summary="List alerts")
@router.get("/", include_in_schema=False)
async def list_alerts(
    camera_id: Optional[int] = None,
    zone: Optional[str] = Query(None, description="Location the job ran under"),
    job_id: Optional[str] = None,
    alert_id: Optional[str] = Query(None, description="Jump straight to one alert"),
    alert_type: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    sort: str = Query("descending", pattern="^(ascending|descending)$"),
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Filtered, paginated alert history with a total count for the pager."""
    if limit < 1 or offset < 0:
        raise HTTPException(400, "limit must be >= 1 and offset >= 0")
    limit = min(limit, MAX_LIMIT)

    conditions = []
    if camera_id is not None:
        conditions.append(JobAlert.camera_id == camera_id)
    if zone:
        conditions.append(JobAlert.zone == zone)
    if job_id:
        conditions.append(JobAlert.job_id == job_id)
    if alert_type:
        conditions.append(JobAlert.alert_type == alert_type)
    if acknowledged is not None:
        conditions.append(JobAlert.acknowledged.is_(acknowledged))
    if alert_id:
        try:
            conditions.append(JobAlert.id == int(alert_id))
        except ValueError:
            raise HTTPException(400, "alert_id must be numeric")
    # at_us is epoch microseconds, so the bounds convert rather than compare.
    if start is not None:
        conditions.append(JobAlert.at_us >= int(_as_utc(start).timestamp() * 1_000_000))
    if end is not None:
        conditions.append(JobAlert.at_us < int(_as_utc(end).timestamp() * 1_000_000))

    count_q = select(func.count(JobAlert.id))
    rows_q = select(JobAlert)
    for c in conditions:
        count_q = count_q.where(c)
        rows_q = rows_q.where(c)

    total = (await db.execute(count_q)).scalar_one()
    order = JobAlert.at_us.asc() if sort == "ascending" else JobAlert.at_us.desc()
    rows = (await db.execute(rows_q.order_by(order).limit(limit).offset(offset))).scalars().all()

    return {
        "count": total,
        "limit": limit,
        "offset": offset,
        "items": [_serialise(a) for a in rows],
    }


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@router.get("/facets", summary="Distinct filter values")
async def alert_facets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Only values that actually appear in the table, so no filter yields zero."""
    async def distinct(col):
        rows = (await db.execute(select(col).where(col.is_not(None)).distinct())).scalars().all()
        return sorted(str(r) for r in rows)

    total = (await db.execute(select(func.count(JobAlert.id)))).scalar_one()
    unack = (await db.execute(
        select(func.count(JobAlert.id)).where(JobAlert.acknowledged.is_(False))
    )).scalar_one()
    cameras = (await db.execute(
        select(JobAlert.camera_id).where(JobAlert.camera_id.is_not(None)).distinct()
    )).scalars().all()

    return {
        "total": total,
        "unacknowledged": unack,
        "cameras": sorted(int(c) for c in cameras),
        "zones": await distinct(JobAlert.zone),
        "jobs": await distinct(JobAlert.job_id),
        "types": await distinct(JobAlert.alert_type),
    }


@router.post("/{alert_id}/ack", summary="Acknowledge an alert")
async def acknowledge_alert(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    alert = await _get_alert(db, alert_id)
    if not alert.acknowledged:
        alert.acknowledged = True
        alert.acknowledged_at = datetime.now(timezone.utc)
        await db.commit()
    return _serialise(alert)


@router.get("/{alert_id}/images", summary="Evidence crops for an alert")
async def alert_images(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Crops of the object that triggered the alert.

    Matched on camera + track id within a window around the alert, which is what
    ties a row in `job_alerts` to files in `shared/ais1` — neither one records
    the other's identifier.
    """
    alert = await _get_alert(db, alert_id)
    at = _at(alert)

    items = artifact_index.scan(resolved_artifacts_dir())
    items = artifact_index.filter_artifacts(
        items,
        kind="image",
        camera_id=alert.camera_id,
        zone=alert.zone or None,
        track_id=alert.track_id,
        start=at - timedelta(seconds=_EVIDENCE_BEFORE_S),
        end=at + timedelta(seconds=_EVIDENCE_AFTER_S),
    )
    # Oldest first reads as a sequence of what happened.
    items = sorted(items, key=lambda m: m.captured_at)[:_MAX_EVIDENCE_IMAGES]

    return {
        "alert_id": str(alert.id),
        "count": len(items),
        "images": [
            {
                "filename": m.filename,
                "url": f"/api/artifacts/file/{m.filename}",
                "captured_at": m.captured_at.isoformat(),
                "class_name": m.class_name,
                "track_id": m.track_id,
            }
            for m in items
        ],
    }


@router.get("/{alert_id}/video", summary="Clip covering an alert")
async def alert_video(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The recorded segment whose time range contains the alert.

    Clip filenames carry the moment the segment OPENED, so the match is the
    latest clip starting at or before the alert — and only if the alert falls
    within that segment's duration.
    """
    alert = await _get_alert(db, alert_id)
    at = _at(alert)

    clips = artifact_index.filter_artifacts(
        artifact_index.scan(resolved_artifacts_dir()),
        kind="clip",
        camera_id=alert.camera_id,
        zone=alert.zone or None,
        end=at + timedelta(seconds=1),
    )
    if not clips:
        raise HTTPException(404, "No recorded clip covers this alert")

    clip = max(clips, key=lambda m: m.captured_at)
    span = (clip.end_frame or 0) - (clip.frame_number or 0)
    # Segments roll every ARTIFACT_SEGMENT_FRAMES ticks; at the pipeline's rate
    # that is a bounded number of seconds, so anything far past the clip's start
    # is not actually inside it.
    max_gap = timedelta(seconds=max(span, 150) / 8.0 + 5)
    if at - clip.captured_at > max_gap:
        raise HTTPException(404, "No recorded clip covers this alert")

    return {
        "alert_id": str(alert.id),
        "filename": clip.filename,
        "video_url": f"/api/artifacts/file/{clip.filename}",
        "thumb_url": f"/api/artifacts/thumb/{clip.filename}",
        "captured_at": clip.captured_at.isoformat(),
    }
