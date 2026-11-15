"""
OmniTrack AI - Data export (CSV / JSON), streamed from the database.

    GET /api/export                     datasets, row counts, whether the caller may export each
    GET /api/export/{dataset}?format=csv|json&since=&until=&camera_id=
        detections, traffic, zone_events, zone_samples, track_visits, fire_incidents,
        emotion, alerts                 -> any logged-in user
        audit, full (zip of everything) -> admin only

/api/export/detections and /api/export/traffic keep their original response (a JSON
object {filename, content, content_type, row_count}) unless `download=true` is
passed, in which case they stream a file like every other dataset.

CSV cells that a spreadsheet would execute as a formula are prefixed with "'".
Every export is written to the audit log.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from sqlalchemy import func, select, text
from starlette.background import BackgroundTask

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.alerts import AlertAck
from app.models.analytics import FootTraffic
from app.models.audit_log import AuditLog
from app.models.detection import Detection
from app.models.user import User, UserRole
from app.models.zones import EmotionAggregate, FireIncident, TrackVisit, ZoneEvent, ZoneSample
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user
from app.security.encryption import try_decrypt_data
from app.services.crud import AuditService
from app.services.export import ExportService

router = APIRouter(prefix="/api/export", tags=["Export"])

_EXPORT_MAX_ROWS = 50_000      # legacy (non-download) detections/traffic responses
_STREAM_BATCH = 2000
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


# ─────────────────────────── dataset definitions ───────────────────────────


def _range(col, since, until, camera_col=None, camera_id=None) -> list:
    conds = []
    if since is not None:
        conds.append(col >= since)
    if until is not None:
        conds.append(col < until)
    if camera_col is not None and camera_id is not None:
        conds.append(camera_col == camera_id)
    return conds


def _q_detections(since, until, cam):
    return (
        select(Detection.id, Detection.camera_id, Detection.timestamp, Detection.class_name, Detection.confidence,
               Detection.track_id, Detection.global_id, Detection.zone, Detection.bbox_x, Detection.bbox_y,
               Detection.bbox_w, Detection.bbox_h)
        .where(*_range(Detection.timestamp, since, until, Detection.camera_id, cam))
        .order_by(Detection.timestamp.desc())
    )


def _q_traffic(since, until, cam):
    hour = func.date_trunc("hour", FootTraffic.timestamp)
    return (
        select(hour.label("hour_start"), FootTraffic.zone, func.max(FootTraffic.person_count),
               func.sum(FootTraffic.direction_in), func.sum(FootTraffic.direction_out))
        .where(*_range(FootTraffic.timestamp, since, until, FootTraffic.camera_id, cam))
        .group_by(hour, FootTraffic.zone)
        .order_by(hour.desc())
    )


def _traffic_row(r) -> list:
    h, zone, peak, d_in, d_out = r
    d_in, d_out = int(d_in or 0), int(d_out or 0)
    return [h, zone, int(peak or 0), d_in, d_out, d_in - d_out]


def _q_zone_events(since, until, cam):
    return (
        select(ZoneEvent.id, ZoneEvent.ts, ZoneEvent.camera_id, ZoneEvent.zone_id, ZoneEvent.event_type,
               ZoneEvent.track_id, ZoneEvent.global_id, ZoneEvent.duration_s, ZoneEvent.value, ZoneEvent.event_uid,
               ZoneEvent.payload)
        .where(*_range(ZoneEvent.ts, since, until, ZoneEvent.camera_id, cam))
        .order_by(ZoneEvent.ts.desc())
    )


def _q_zone_samples(since, until, cam):
    return (
        select(ZoneSample.id, ZoneSample.ts, ZoneSample.camera_id, ZoneSample.zone_id, ZoneSample.count,
               ZoneSample.raw_count, ZoneSample.capacity, ZoneSample.occupancy_pct, ZoneSample.level)
        .where(*_range(ZoneSample.ts, since, until, ZoneSample.camera_id, cam))
        .order_by(ZoneSample.ts.desc())
    )


def _q_track_visits(since, until, cam):
    return (
        select(TrackVisit.id, TrackVisit.camera_id, TrackVisit.track_id, TrackVisit.global_id,
               TrackVisit.first_seen, TrackVisit.last_seen, TrackVisit.duration_s)
        .where(*_range(TrackVisit.first_seen, since, until, TrackVisit.camera_id, cam))
        .order_by(TrackVisit.first_seen.desc())
    )


def _q_fire(since, until, cam):
    return (
        select(FireIncident.id, FireIncident.incident_key, FireIncident.camera_id, FireIncident.status,
               FireIncident.started_at, FireIncident.last_hit_at, FireIncident.cleared_at, FireIncident.peak_conf,
               FireIncident.class_name, FireIncident.hits, FireIncident.retriggers, FireIncident.bbox,
               FireIncident.frame_wh)
        .where(*_range(FireIncident.started_at, since, until, FireIncident.camera_id, cam))
        .order_by(FireIncident.started_at.desc())
    )


def _q_emotion(since, until, cam):
    return (
        select(EmotionAggregate.id, EmotionAggregate.camera_id, EmotionAggregate.zone_id,
               EmotionAggregate.bucket_start, EmotionAggregate.bucket_s, EmotionAggregate.total,
               EmotionAggregate.sentiment, EmotionAggregate.dominant, EmotionAggregate.counts)
        .where(*_range(EmotionAggregate.bucket_start, since, until, EmotionAggregate.camera_id, cam))
        .order_by(EmotionAggregate.bucket_start.desc())
    )


def _q_alerts(since, until, cam):
    from app.routers.alerts import _union  # noqa: PLC0415

    u = _union()
    return (
        select(u.c.source, u.c.id, u.c.ts, u.c.camera_id, u.c.zone_id, u.c.kind, u.c.acked,
               AlertAck.acknowledged_at, User.username, AlertAck.note)
        .select_from(u)
        .outerjoin(AlertAck, (AlertAck.source == u.c.source) & (AlertAck.source_id == u.c.id))
        .outerjoin(User, User.id == AlertAck.acknowledged_by)
        .where(*_range(u.c.ts, since, until, u.c.camera_id, cam))
        .order_by(u.c.ts.desc())
    )


def _q_audit(since, until, cam):
    return (
        select(AuditLog.id, AuditLog.timestamp, AuditLog.event_type, AuditLog.user_id, AuditLog.description,
               AuditLog.ip_address, AuditLog.user_agent, AuditLog.encrypted_metadata, AuditLog.hash_alg,
               AuditLog.previous_hash, AuditLog.current_hash)
        .where(*_range(AuditLog.timestamp, since, until))
        .order_by(AuditLog.id)
    )


def _audit_row(r) -> list:
    r = list(r)
    r[7] = try_decrypt_data(r[7])
    return r


# name -> (columns, query builder, row transform, admin only, count table)
Dataset = Tuple[List[str], Callable, Optional[Callable], bool, Any]
DATASETS: Dict[str, Dataset] = {
    "detections": (["id", "camera_id", "timestamp", "class_name", "confidence", "track_id", "global_id", "zone",
                    "bbox_x", "bbox_y", "bbox_w", "bbox_h"], _q_detections, None, False, Detection),
    "traffic": (["hour_start", "zone", "person_count", "direction_in", "direction_out", "net_flow"],
                _q_traffic, _traffic_row, False, FootTraffic),
    "zone_events": (["id", "ts", "camera_id", "zone_id", "event_type", "track_id", "global_id", "duration_s", "value",
                     "event_uid", "payload"], _q_zone_events, None, False, ZoneEvent),
    "zone_samples": (["id", "ts", "camera_id", "zone_id", "count", "raw_count", "capacity", "occupancy_pct", "level"],
                     _q_zone_samples, None, False, ZoneSample),
    "track_visits": (["id", "camera_id", "track_id", "global_id", "first_seen", "last_seen", "duration_s"],
                     _q_track_visits, None, False, TrackVisit),
    "fire_incidents": (["id", "incident_key", "camera_id", "status", "started_at", "last_hit_at", "cleared_at",
                        "peak_conf", "class_name", "hits", "retriggers", "bbox_xywh", "frame_wh"],
                       _q_fire, None, False, FireIncident),
    "emotion": (["id", "camera_id", "zone_id", "bucket_start", "bucket_s", "total", "sentiment", "dominant", "counts"],
                _q_emotion, None, False, EmotionAggregate),
    "alerts": (["source", "id", "ts", "camera_id", "zone_id", "kind", "acked", "acked_at", "acked_by", "ack_note"],
               _q_alerts, None, False, None),
    "audit": (["id", "timestamp", "event_type", "user_id", "description", "ip_address", "user_agent", "metadata",
               "hash_alg", "previous_hash", "current_hash"], _q_audit, _audit_row, True, AuditLog),
}
ADMIN_ONLY = {"audit", "full"}
DatasetName = Literal["detections", "traffic", "zone_events", "zone_samples", "track_visits", "fire_incidents",
                      "emotion", "alerts", "audit", "full"]


# ─────────────────────────── serialisation ───────────────────────────


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), default=str)
    if isinstance(v, str) and v.startswith(_FORMULA_PREFIX):
        return "'" + v
    return v


def _json_val(v: Any) -> Any:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return v


def _csv_line(values: Sequence[Any]) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerow([_cell(v) for v in values])
    return buf.getvalue()


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


async def _rows(name: str, since, until, cam) -> AsyncIterator[list]:
    cols, build, transform, _admin, _t = DATASETS[name]
    async with AsyncSessionLocal() as session:
        result = await session.stream(build(since, until, cam).execution_options(yield_per=_STREAM_BATCH))
        async for part in result.partitions():
            for r in part:
                yield transform(r) if transform else list(r)


async def _csv_stream(name: str, since, until, cam) -> AsyncIterator[bytes]:
    yield _csv_line(DATASETS[name][0]).encode()
    batch: List[str] = []
    async for row in _rows(name, since, until, cam):
        batch.append(_csv_line(row))
        if len(batch) >= 500:
            yield "".join(batch).encode()
            batch = []
    if batch:
        yield "".join(batch).encode()


async def _json_stream(name: str, since, until, cam) -> AsyncIterator[bytes]:
    cols = DATASETS[name][0]
    yield b"["
    first = True
    async for row in _rows(name, since, until, cam):
        item = json.dumps({c: _json_val(v) for c, v in zip(cols, row)}, default=str)
        yield (item if first else "," + item).encode()
        first = False
    yield b"]"


def _filename(name: str, ext: str) -> str:
    return f"omnitrack_{name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{ext}"


async def _audit_export(request: Request, user: User, name: str, fmt: str, since, until, cam) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await AuditService.log_event(
                db, "data_export", user.id, f"Exported {name} as {fmt}",
                metadata={"dataset": name, "format": fmt, "since": since.isoformat() if since else None,
                          "until": until.isoformat() if until else None, "camera_id": cam},
                ip_address=client_ip(request), user_agent=request.headers.get("user-agent"),
            )
            await db.commit()
    except Exception as exc:  # export still proceeds; the failure is visible in logs
        logger.error(f"export: audit log write failed: {exc}")


async def _build_zip(since, until, cam) -> str:
    out_dir = Path(settings.EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="omnitrack_full_", suffix=".zip", dir=str(out_dir))
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in DATASETS:
                with zf.open(f"{name}.csv", "w", force_zip64=True) as fh:
                    async for chunk in _csv_stream(name, since, until, cam):
                        fh.write(chunk)
    except Exception:
        os.unlink(path)
        raise
    return path


def _file_response(name: str, fmt: str, since, until, cam) -> StreamingResponse:
    if fmt == "json":
        body, media, ext = _json_stream(name, since, until, cam), "application/json", "json"
    else:
        body, media, ext = _csv_stream(name, since, until, cam), "text/csv; charset=utf-8", "csv"
    return StreamingResponse(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{_filename(name, ext)}"'}
    )


# ─────────────────────────── endpoints ───────────────────────────


@router.get("")
async def list_datasets(user: User = Depends(get_current_user)):
    is_admin = user.role == UserRole.ADMIN
    out = []
    for name in [*DATASETS, "full"]:
        rows: Optional[int] = None
        table = DATASETS[name][4] if name in DATASETS else None
        if name != "full":
            try:
                async with AsyncSessionLocal() as db:
                    await db.execute(text("SET LOCAL statement_timeout = 3000"))
                    if table is not None:
                        rows = int((await db.execute(select(func.count()).select_from(table))).scalar_one())
                    else:
                        q = DATASETS[name][1](None, None, None).subquery()
                        rows = int((await db.execute(select(func.count()).select_from(q))).scalar_one())
            except Exception as exc:
                logger.debug(f"export: count for {name} unavailable: {exc}")
        out.append({
            "dataset": name, "rows": rows, "admin_only": name in ADMIN_ONLY,
            "allowed": is_admin or name not in ADMIN_ONLY, "formats": ["zip"] if name == "full" else ["csv", "json"],
        })
    return {"datasets": out}


@router.get("/detections")
async def export_detections(
    request: Request,
    format: Literal["csv", "json"] = "csv",
    camera_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(10_000, ge=1, le=_EXPORT_MAX_ROWS),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    download: bool = False,
    current_user: User = Depends(get_current_user),
):
    """Legacy response: the most recent detections as {filename, content, ...}; download=true streams a file."""
    since, until = _aware(since), _aware(until)
    await _audit_export(request, current_user, "detections", format, since, until, camera_id)
    if download:
        return _file_response("detections", format, since, until, camera_id)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(_q_detections(since, until, camera_id).limit(limit))).all()
    data = [
        {"id": r[0], "camera_id": r[1], "timestamp": r[2].isoformat() if r[2] else "", "class_name": r[3],
         "confidence": r[4], "track_id": r[5], "global_id": r[6], "zone": r[7], "bbox_x": r[8], "bbox_y": r[9],
         "bbox_w": r[10], "bbox_h": r[11]}
        for r in rows
    ]
    if format == "json":
        return ExportService.to_json(data, "detections")
    return ExportService.detection_report(data)


@router.get("/traffic")
async def export_traffic(
    request: Request,
    format: Literal["csv", "json"] = "csv",
    camera_id: Optional[int] = Query(None, ge=1),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    download: bool = False,
    current_user: User = Depends(get_current_user),
):
    """Legacy response: hourly foot traffic as {filename, content, ...}; download=true streams a file."""
    since, until = _aware(since), _aware(until)
    await _audit_export(request, current_user, "traffic", format, since, until, camera_id)
    if download:
        return _file_response("traffic", format, since, until, camera_id)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(_q_traffic(since, until, camera_id).limit(_EXPORT_MAX_ROWS))).all()
    data = [
        {"date": h.date().isoformat(), "hour": h.hour, "zone": zone, "person_count": int(peak or 0),
         "direction_in": int(d_in or 0), "direction_out": int(d_out or 0)}
        for h, zone, peak, d_in, d_out in rows
    ]
    if format == "json":
        return ExportService.to_json(data, "traffic")
    return ExportService.traffic_report(data)


@router.get("/{dataset}")
async def export_dataset(
    dataset: DatasetName,
    request: Request,
    format: Literal["csv", "json", "zip"] = "csv",
    camera_id: Optional[int] = Query(None, ge=1),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    download: bool = True,   # accepted for symmetry with /detections and /traffic; always a file here
    current_user: User = Depends(get_current_user),
):
    if dataset in ADMIN_ONLY and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Requires role: admin")
    since, until = _aware(since), _aware(until)
    if dataset == "full":
        await _audit_export(request, current_user, "full", "zip", since, until, camera_id)
        path = await _build_zip(since, until, camera_id)
        return FileResponse(path, media_type="application/zip", filename=_filename("full", "zip"),
                            background=BackgroundTask(os.unlink, path))
    if format == "zip":
        raise HTTPException(status_code=422, detail="zip is only available for the 'full' dataset")
    await _audit_export(request, current_user, dataset, format, since, until, camera_id)
    return _file_response(dataset, format, since, until, camera_id)
