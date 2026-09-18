"""
OmniTrack AI — Data Export Endpoints
════════════════════════════════════

Serves the reports in `app/services/export.py` as real file downloads.

The service layer was already complete; what was missing was the wiring. The two
endpoints that existed (`/api/export/detections`, `/api/export/traffic`) lived in
main.py and returned a hardcoded `sample` list with a "TODO: pull real data from
DB" comment, so a store with 123k detections exported exactly one invented row.
Every endpoint here queries the database.

Shape of each route:
  format=csv   -> text/csv  with Content-Disposition: attachment
  format=json  -> application/json, same rows, same filter semantics

Filters (where the underlying table supports them):
  start, end   ISO-8601 timestamps, inclusive lower / exclusive upper bound
  camera_id    restrict to one camera
  limit        row cap (default 10_000, hard max 100_000)

Why a limit by default: `detections` alone is six figures and a browser download
of the whole table is rarely what someone wants. Pass limit explicitly to widen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.analytics import (
    CustomerJourney,
    DemographicSnapshot,
    FootTraffic,
    StoreVibeScore,
)
from app.models.audit_log import AuditLog
from app.models.detection import Detection
from app.models.job import JobAlert, LinePassingCount, RoiDwell
from app.models.user import User
from app.security.dependencies import get_current_user
from app.services.crud import AuditService
from app.services.export import ExportService

router = APIRouter(prefix="/api/export", tags=["Export"])

MAX_LIMIT = 100_000
DEFAULT_LIMIT = 10_000


# ── helpers ────────────────────────────────────────────────────────────

def _download(payload: Dict[str, Any]) -> Response:
    """Turn an ExportService payload into an actual browser download."""
    return Response(
        content=payload["content"],
        media_type=payload["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{payload["filename"]}"',
            # The row count is useful to a caller scripting against the API and
            # costs nothing; the body is a file, so it cannot carry metadata.
            "X-Row-Count": str(payload.get("row_count", "")),
            "Cache-Control": "no-store",
        },
    )


def _as_dicts(rows: List[Any]) -> List[Dict[str, Any]]:
    """ORM objects -> plain dicts the ExportService formatters expect."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = {
            c.key: getattr(r, c.key)
            for c in r.__table__.columns  # type: ignore[attr-defined]
        }
        # Detection maps the `metadata` column to the attribute `metadata_`;
        # column-based iteration yields the DB name, which is not an attribute.
        d.pop("metadata", None)
        out.append(d)
    return out


def _windowed(query, model, start: Optional[datetime], end: Optional[datetime],
              camera_id: Optional[int], ts_column: str = "timestamp"):
    """Apply the shared start/end/camera filters when the model supports them."""
    col = getattr(model, ts_column, None)
    if col is not None:
        if start is not None:
            query = query.where(col >= start)
        if end is not None:
            query = query.where(col < end)
    cam = getattr(model, "camera_id", None)
    if camera_id is not None and cam is not None:
        query = query.where(cam == camera_id)
    return query


def _clamp(limit: int) -> int:
    if limit < 1:
        raise HTTPException(400, "limit must be >= 1")
    return min(limit, MAX_LIMIT)


async def _fetch(
    db: AsyncSession, model, order_col, start, end, camera_id, limit,
    ts_column: str = "timestamp",
) -> List[Dict[str, Any]]:
    q = _windowed(select(model), model, start, end, camera_id, ts_column)
    q = q.order_by(order_col.desc()).limit(_clamp(limit))
    return _as_dicts((await db.execute(q)).scalars().all())


def _emit(fmt: str, rows: List[Dict[str, Any]], formatter, name: str) -> Response:
    if fmt not in ("csv", "json"):
        raise HTTPException(400, "format must be 'csv' or 'json'")
    if fmt == "json":
        payload = ExportService.to_json(rows, name)
        payload["row_count"] = len(rows)
        return _download(payload)
    return _download(formatter(rows))


# ── endpoints ──────────────────────────────────────────────────────────

@router.get("/detections", summary="Detection log")
async def export_detections(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    camera_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every detection the pipeline persisted, newest first."""
    rows = await _fetch(db, Detection, Detection.timestamp, start, end, camera_id, limit)
    return _emit(format, rows, ExportService.detection_report, "detections")


@router.get("/traffic", summary="Foot traffic report")
async def export_traffic(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    camera_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-zone foot traffic intervals with in/out direction counts."""
    rows = await _fetch(db, FootTraffic, FootTraffic.timestamp, start, end, camera_id, limit)
    for r in rows:                      # the formatter expects date/hour columns
        ts = r.get("timestamp")
        r["date"] = ts.date().isoformat() if ts else ""
        r["hour"] = ts.hour if ts else ""
    return _emit(format, rows, ExportService.traffic_report, "foot_traffic")


@router.get("/line-counts", summary="Line crossing counts")
async def export_line_counts(
    format: str = Query("csv", pattern="^(csv|json)$"),
    job_id: Optional[str] = None,
    camera_id: Optional[int] = None,
    latest_per_track: bool = True,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Line-passing KPI output.

    The counters on each row are CUMULATIVE for that track, so the table holds
    many rows per track as the count climbs. `latest_per_track=true` (the
    default) keeps only each track's final row via DISTINCT ON, which is the
    form you want for totals — the same shape the live dashboard reads. Set it
    false to export the full history for debugging a count.
    """
    q = select(LinePassingCount)
    if job_id:
        q = q.where(LinePassingCount.job_id == job_id)
    if camera_id is not None:
        q = q.where(LinePassingCount.camera_id == camera_id)

    if latest_per_track:
        q = q.distinct(LinePassingCount.track_id, LinePassingCount.region_name).order_by(
            LinePassingCount.track_id,
            LinePassingCount.region_name,
            LinePassingCount.at_us.desc(),
        )
    else:
        q = q.order_by(LinePassingCount.at_us.desc())

    rows = _as_dicts((await db.execute(q.limit(_clamp(limit)))).scalars().all())
    return _emit(format, rows, ExportService.line_passing_report, "line_counts")


@router.get("/roi-dwell", summary="ROI dwell times")
async def export_roi_dwell(
    format: str = Query("csv", pattern="^(csv|json)$"),
    job_id: Optional[str] = None,
    camera_id: Optional[int] = None,
    latest_per_track: bool = True,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Region-of-interest dwell accumulation, same cumulative semantics as lines."""
    q = select(RoiDwell)
    if job_id:
        q = q.where(RoiDwell.job_id == job_id)
    if camera_id is not None:
        q = q.where(RoiDwell.camera_id == camera_id)

    if latest_per_track:
        q = q.distinct(RoiDwell.track_id, RoiDwell.region_name).order_by(
            RoiDwell.track_id, RoiDwell.region_name, RoiDwell.at_us.desc(),
        )
    else:
        q = q.order_by(RoiDwell.at_us.desc())

    rows = _as_dicts((await db.execute(q.limit(_clamp(limit)))).scalars().all())
    return _emit(format, rows, ExportService.roi_dwell_report, "roi_dwell")


@router.get("/alerts", summary="Job alerts")
async def export_alerts(
    format: str = Query("csv", pattern="^(csv|json)$"),
    job_id: Optional[str] = None,
    camera_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Threshold alerts raised by running jobs (dwell, crowding, fire)."""
    q = select(JobAlert)
    if job_id:
        q = q.where(JobAlert.job_id == job_id)
    if camera_id is not None:
        q = q.where(JobAlert.camera_id == camera_id)
    q = q.order_by(JobAlert.at_us.desc()).limit(_clamp(limit))
    rows = _as_dicts((await db.execute(q)).scalars().all())
    return _emit(format, rows, ExportService.alerts_report, "alerts")


@router.get("/journeys", summary="Customer journeys")
async def export_journeys(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    camera_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cross-camera journeys, one row per leg, keyed by Re-ID global_id."""
    rows = await _fetch(
        db, CustomerJourney, CustomerJourney.date, start, end, camera_id, limit,
        ts_column="date",
    )
    return _emit(format, rows, ExportService.journeys_report, "journeys")


@router.get("/demographics", summary="Demographics summary")
async def export_demographics(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    camera_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Age/gender snapshots. Empty until a feed with faces has been processed."""
    rows = await _fetch(
        db, DemographicSnapshot, DemographicSnapshot.timestamp,
        start, end, camera_id, limit,
    )
    for r in rows:
        ts = r.get("timestamp")
        r["date"] = ts.date().isoformat() if ts else ""
    return _emit(format, rows, ExportService.demographics_report, "demographics")


@router.get("/vibe", summary="Store vibe history")
async def export_vibe(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store Vibe Score readings over time."""
    rows = await _fetch(
        db, StoreVibeScore, StoreVibeScore.timestamp, start, end, None, limit,
    )
    return _emit(format, rows, ExportService.vibe_history_report, "vibe_history")


@router.get("/audit", summary="Audit trail")
async def export_audit(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = DEFAULT_LIMIT,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Tamper-evident audit trail.

    Encrypted metadata is deliberately NOT exported — it is AES-encrypted at rest
    precisely so it does not leave the system in a CSV. The chain is verified
    once for the whole export and the verdict stamped on every row, so a reader
    can tell whether the file came from an intact chain.
    """
    rows = await _fetch(db, AuditLog, AuditLog.timestamp, start, end, None, limit)
    status = await AuditService.verify_integrity(db)
    for r in rows:
        r.pop("encrypted_metadata", None)
        r["is_valid"] = status["valid"]
    if format == "json":
        payload = ExportService.to_json(
            {"chain_status": status, "entries": rows}, "audit_trail"
        )
        payload["row_count"] = len(rows)
        return _download(payload)
    return _download(ExportService.audit_report(rows))


@router.get("/full", summary="Full store report (JSON)")
async def export_full(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    camera_id: Optional[int] = None,
    limit: int = 1_000,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Everything in one JSON bundle — the "give me the lot" export.

    The per-section limit is deliberately lower than the single-table endpoints:
    this is a summary artefact, not a database dump. Use the individual routes
    when you want the full table.
    """
    n = _clamp(limit)
    detections = await _fetch(db, Detection, Detection.timestamp, start, end, camera_id, n)
    traffic = await _fetch(db, FootTraffic, FootTraffic.timestamp, start, end, camera_id, n)
    demographics = await _fetch(
        db, DemographicSnapshot, DemographicSnapshot.timestamp, start, end, camera_id, n
    )
    vibe = await _fetch(db, StoreVibeScore, StoreVibeScore.timestamp, start, end, None, n)

    payload = ExportService.full_store_report(detections, traffic, demographics, vibe)
    payload["row_count"] = len(detections) + len(traffic) + len(demographics) + len(vibe)
    return _download(payload)
