"""
OmniTrack AI - Zones API (Remediation Plan, Phase 3).

Zones are drawn per camera on a live snapshot, stored normalised (0..1), and
drive every zone analytic. Reads need a logged-in user; create, update and
delete are admin-only. Every mutation reloads the camera's zones into the
analytics engine and publishes the in-process "zones_changed" {camera_id}
event so the pipeline can react.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import definitions as d
from app.analytics.engine import HEATMAP_GRID, get_engine
from app.analytics.zones_repo import HEATMAP_ZONE, load_all_zones, load_zones, zone_to_dict
from app.database import get_db
from app.models.analytics import FootTraffic
from app.models.camera import Camera
from app.models.user import User, UserRole
from app.models.zones import FireIncident, Zone, ZoneEvent
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user, require_role
from app.services.crud import AuditService
from app.services.event_bus import publish_local

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zones", tags=["Zones"])

ZoneType = Literal["crowd", "shelf", "queue", "counter", "entry_line", "restricted"]
MAX_ZONES_PER_CAMERA = 32
SNAPSHOT_TIMEOUT_S = 8.0

ZONE_TYPE_INFO = [
    {"type": "crowd", "label": "Crowd area", "geometry": "polygon", "needs_capacity": True,
     "measures": "Occupancy (3 s median) and level vs capacity: low 10 %, moderate 30 %, high 60 %, critical 90 %."},
    {"type": "shelf", "label": "Shelf / display", "geometry": "polygon", "needs_capacity": False,
     "measures": "Visits (>= 3 s inside), dwell, engaged visits (> 10 s), attention score."},
    {"type": "queue", "label": "Queue", "geometry": "polygon", "needs_capacity": False,
     "measures": "Queue length (2 s median); wait = queue entry to counter entry (pair by lane)."},
    {"type": "counter", "label": "Counter / till", "geometry": "polygon", "needs_capacity": False,
     "measures": "Service time = time at the counter; throughput = counter exits per hour."},
    {"type": "entry_line", "label": "Entry line", "geometry": "line", "needs_capacity": False,
     "measures": "Footfall: people crossing the line inward (in_sign picks the inside)."},
    {"type": "restricted", "label": "Restricted area", "geometry": "polygon", "needs_capacity": False,
     "measures": "Intrusion alert with snapshot and clip when a person is inside for 2 s or more."},
]


class ZoneIn(BaseModel):
    camera_id: int
    name: str = Field(min_length=1, max_length=100)
    zone_type: ZoneType
    points: List[List[float]]
    capacity: Optional[int] = Field(default=None, ge=1, le=10000)
    lane: Optional[str] = Field(default=None, max_length=50)
    in_sign: Optional[int] = None
    dwell_alert_seconds: Optional[float] = Field(
        default=None, ge=d.DWELL_ALERT_MIN_S, le=d.DWELL_ALERT_MAX_S,
        description="Alert when one person stays in the zone longer than this (not for entry lines).",
    )
    color: Optional[str] = Field(default=None, max_length=20)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @model_validator(mode="after")
    def _check(self) -> "ZoneIn":
        self.points = d.validate_polygon(self.points, self.zone_type)
        if self.zone_type == "crowd" and not self.capacity:
            raise ValueError("crowd zones need a capacity (people at 100 %)")
        if self.zone_type != "crowd":
            self.capacity = None
        if self.zone_type not in ("queue", "counter"):
            self.lane = None
        elif self.lane is not None:
            self.lane = self.lane.strip() or None
        if self.in_sign not in (None, 1, -1):
            raise ValueError("in_sign must be 1 or -1")
        if self.zone_type == "restricted":
            self.dwell_alert_seconds = None   # a restricted zone alerts on entry; no dwell limit
        if self.zone_type == "entry_line" and self.dwell_alert_seconds is not None:
            raise ValueError("dwell_alert_seconds is not available for entry lines")
        return self


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    zone_type: Optional[ZoneType] = None
    points: Optional[List[List[float]]] = None
    capacity: Optional[int] = Field(default=None, ge=1, le=10000)
    lane: Optional[str] = Field(default=None, max_length=50)
    in_sign: Optional[int] = None
    dwell_alert_seconds: Optional[float] = Field(default=None, ge=d.DWELL_ALERT_MIN_S, le=d.DWELL_ALERT_MAX_S)
    color: Optional[str] = Field(default=None, max_length=20)
    is_active: Optional[bool] = None


# ---------------------------------------------------------------------------
# Engine sync
# ---------------------------------------------------------------------------


async def sync_camera_zones(camera_id: int) -> List[dict]:
    """Reload one camera's zones into the engine and announce the change."""
    zones = await load_zones(str(camera_id))
    get_engine().set_zones(str(camera_id), zones)
    publish_local("zones_changed", {"camera_id": camera_id})
    return zones


async def sync_all_zones() -> int:
    """Load every camera's zones into the engine (called at startup)."""
    by_cam = await load_all_zones()
    engine = get_engine()
    for cam_id, zones in by_cam.items():
        engine.set_zones(str(cam_id), zones)
    return sum(len(z) for z in by_cam.values())


def _zone_config(body: ZoneIn) -> Optional[Dict[str, Any]]:
    cfg: Dict[str, Any] = {}
    if body.zone_type == "entry_line":
        cfg["in_sign"] = body.in_sign or 1
    elif body.dwell_alert_seconds is not None:
        cfg["dwell_alert_seconds"] = float(body.dwell_alert_seconds)
    return cfg or None


def _payload(body: ZoneIn) -> Dict[str, Any]:
    return {
        "camera_id": body.camera_id,
        "name": body.name,
        "zone_type": body.zone_type,
        "points": body.points,
        "capacity": body.capacity,
        "lane": body.lane,
        "config": _zone_config(body),
        "color": body.color,
        "is_active": body.is_active,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/types")
async def zone_types(_: User = Depends(get_current_user)):
    return {"types": ZONE_TYPE_INFO}


@router.get("")
async def list_zones(
    camera_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Zone).order_by(Zone.camera_id, Zone.id)
    if camera_id is not None:
        q = q.where(Zone.camera_id == camera_id)
    rows = (await db.execute(q)).scalars().all()
    return {"zones": [zone_to_dict(z) for z in rows], "count": len(rows),
            "status": "ok" if rows else "no_data"}


@router.get("/{zone_id:int}")
async def get_zone(zone_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    z = await db.get(Zone, zone_id)
    if z is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone_to_dict(z)


@router.post("", status_code=201)
async def create_zone(
    body: ZoneIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role([UserRole.ADMIN])),
):
    if await db.get(Camera, body.camera_id) is None:
        raise HTTPException(status_code=404, detail=f"Camera {body.camera_id} not found")
    count = (await db.execute(select(func.count(Zone.id)).where(Zone.camera_id == body.camera_id))).scalar_one()
    if count >= MAX_ZONES_PER_CAMERA:
        raise HTTPException(status_code=409, detail=f"A camera can have at most {MAX_ZONES_PER_CAMERA} zones")
    zone = Zone(**_payload(body))
    db.add(zone)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Camera {body.camera_id} already has a zone named '{body.name}'")
    await AuditService.log_event(
        db, "ZONE_CREATE", admin.id, f"Zone created: {zone.name}",
        {"zone_id": zone.id, "camera_id": zone.camera_id, "zone_type": zone.zone_type},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(zone)
    await sync_camera_zones(zone.camera_id)
    return zone_to_dict(zone)


@router.put("/{zone_id:int}")
async def update_zone(
    zone_id: int,
    body: ZoneUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role([UserRole.ADMIN])),
):
    zone = await db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    current = zone_to_dict(zone)
    patch = body.model_dump(exclude_unset=True)
    merged = {
        "camera_id": zone.camera_id,
        "name": patch.get("name", current["name"]),
        "zone_type": patch.get("zone_type", current["zone_type"]),
        "points": patch.get("points", current["points"]),
        "capacity": patch["capacity"] if "capacity" in patch else current["capacity"],
        "lane": patch["lane"] if "lane" in patch else current["lane"],
        "in_sign": patch.get("in_sign", (current["config"] or {}).get("in_sign")),
        "dwell_alert_seconds": patch["dwell_alert_seconds"] if "dwell_alert_seconds" in patch
        else (None if patch.get("zone_type") == "entry_line"
              else (current["config"] or {}).get("dwell_alert_seconds")),
        "color": patch.get("color", current["color"]),
        "is_active": patch.get("is_active", current["is_active"]),
    }
    try:
        validated = ZoneIn(**merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    for k, v in _payload(validated).items():
        setattr(zone, k, v)
    camera_id = zone.camera_id
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Camera {camera_id} already has a zone named '{merged['name']}'")
    await AuditService.log_event(
        db, "ZONE_UPDATE", admin.id, f"Zone updated: {zone.name}",
        {"zone_id": zone.id, "camera_id": camera_id, "fields": sorted(patch)},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(zone)
    await sync_camera_zones(zone.camera_id)
    return zone_to_dict(zone)


@router.delete("/{zone_id:int}")
async def delete_zone(
    zone_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role([UserRole.ADMIN])),
):
    zone = await db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    camera_id, name = zone.camera_id, zone.name
    await db.delete(zone)
    await AuditService.log_event(
        db, "ZONE_DELETE", admin.id, f"Zone deleted: {name}",
        {"zone_id": zone_id, "camera_id": camera_id},
        ip_address=client_ip(request),
    )
    await db.commit()
    await sync_camera_zones(camera_id)
    return {"deleted": zone_id, "camera_id": camera_id}


# ---------------------------------------------------------------------------
# Live state, alerts and the editor snapshot
# ---------------------------------------------------------------------------


@router.get("/live")
async def live_state(camera_id: Optional[int] = Query(None), _: User = Depends(get_current_user)):
    """Current engine state per camera (smoothed counts, levels, lanes, fire)."""
    engine = get_engine()
    ids = [str(camera_id)] if camera_id is not None else engine.camera_ids()
    cams = [engine.snapshot(c) for c in ids]
    live = [c for c in cams if c.get("status") == "ok"]
    return {"cameras": cams, "vibe": engine.latest_vibe(), "status": "ok" if live else "no_data"}


@router.get("/alerts")
async def zone_alerts(
    hours: int = Query(24, ge=1, le=24 * 30),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Overcrowding / long-queue alerts and fire incidents in the window."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    alert_rows = (
        await db.execute(
            select(ZoneEvent, Zone.name)
            .join(Zone, Zone.id == ZoneEvent.zone_id, isouter=True)
            .where(and_(ZoneEvent.event_type == "alert", ZoneEvent.ts >= since))
            .order_by(desc(ZoneEvent.ts))
            .limit(limit)
        )
    ).all()
    fire_rows = (
        await db.execute(
            select(FireIncident).where(FireIncident.started_at >= since).order_by(desc(FireIncident.started_at)).limit(limit)
        )
    ).scalars().all()
    alerts = [
        {
            "id": ev.id, "camera_id": ev.camera_id, "zone_id": ev.zone_id, "zone_name": name,
            "alert_type": (ev.payload or {}).get("alert_type"), "action": (ev.payload or {}).get("action"),
            "ts": ev.ts.isoformat(), "value": ev.value, "details": ev.payload,
        }
        for ev, name in alert_rows
    ]
    fires = [fire_incident_dict(f) for f in fire_rows]
    return {"alerts": alerts, "fire_incidents": fires, "window_hours": hours,
            "status": "ok" if (alerts or fires) else "no_data"}


def fire_incident_dict(f: FireIncident) -> dict:
    return {
        "id": f.id, "incident_key": f.incident_key, "camera_id": f.camera_id, "status": f.status,
        "started_at": f.started_at.isoformat() if f.started_at else None,
        "last_hit_at": f.last_hit_at.isoformat() if f.last_hit_at else None,
        "cleared_at": f.cleared_at.isoformat() if f.cleared_at else None,
        "peak_conf": f.peak_conf, "class_name": f.class_name, "bbox": f.bbox, "frame_wh": f.frame_wh,
        "hits": f.hits, "retriggers": f.retriggers,
        "duration_s": round(((f.cleared_at or f.last_hit_at or f.started_at) - f.started_at).total_seconds(), 1)
        if f.started_at else None,
    }


def _grab_frame_jpeg(source: str) -> Optional[bytes]:
    """Read one frame from a camera source (file, RTSP/HTTP URL or webcam index)."""
    import cv2

    src: Any = int(source) if source.isdigit() else source
    if isinstance(src, str) and "://" not in src and not os.path.exists(src):
        return None
    cap = cv2.VideoCapture(src)
    try:
        if not cap.isOpened():
            return None
        frame = None
        # Skip the first frames of files/streams (often black or a keyframe artefact).
        for _ in range(5):
            ok, f = cap.read()
            if not ok:
                break
            frame = f
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else None
    finally:
        cap.release()


@router.get("/cameras/{camera_id}/snapshot")
async def camera_snapshot(
    camera_id: int,
    request: Request,
    clean: bool = Query(False, description="Live frame without detection overlays"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """A JPEG of the camera to draw zones on (live frame if the camera runs)."""
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is not None:
        try:
            jpeg = (pipeline.get_latest_clean_jpeg(camera_id) if clean
                    else pipeline.get_latest_annotated_jpeg(camera_id))
        except Exception:
            jpeg = None
        if jpeg:
            return Response(content=jpeg, media_type="image/jpeg", headers={"X-Snapshot-Source": "live", "Cache-Control": "no-store"})
    source = None
    if pipeline is not None:
        source = (getattr(pipeline, "_camera_sources", {}) or {}).get(camera_id)
    if not source:
        cam = await db.get(Camera, camera_id)
        if cam is None:
            raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
        source = cam.stream_url
    if not source or str(source).startswith("pipeline://"):
        raise HTTPException(status_code=404, detail="Camera has no readable source; start it to get a snapshot")
    try:
        jpeg = await asyncio.wait_for(asyncio.to_thread(_grab_frame_jpeg, str(source)), timeout=SNAPSHOT_TIMEOUT_S)
    except asyncio.TimeoutError:
        jpeg = None
    if not jpeg:
        raise HTTPException(status_code=503, detail="Could not read a frame from the camera source")
    return Response(content=jpeg, media_type="image/jpeg", headers={"X-Snapshot-Source": "source", "Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/heatmap")
async def camera_heatmap(
    camera_id: int,
    minutes: int = Query(60, ge=1, le=7 * 24 * 60),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Spatial foot-traffic heatmap: person-seconds per grid cell over the last ``minutes``.

    Sums the persisted 1-min buckets (``foot_traffic`` rows) and the live, not yet
    persisted bucket. Cells are row-major over the normalised frame.
    """
    if await db.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=minutes)
    rows = (await db.execute(
        select(FootTraffic.heatmap_data, FootTraffic.person_count, FootTraffic.interval_start)
        .where(FootTraffic.camera_id == camera_id, FootTraffic.zone == HEATMAP_ZONE,
               FootTraffic.interval_start >= since)
        .order_by(FootTraffic.interval_start)
    )).all()

    gw, gh = HEATMAP_GRID
    values = [0.0] * (gw * gh)
    buckets, frame_wh, first, last = 0, None, None, None
    seen_starts = set()

    def add(data: dict, start: Optional[datetime]) -> None:
        nonlocal buckets, frame_wh, first, last
        if not data or list(data.get("grid") or []) != [gw, gh]:
            return  # a bucket from a different grid size is not comparable
        for idx, v in data.get("cells") or []:
            if 0 <= int(idx) < len(values):
                values[int(idx)] += float(v)
        buckets += 1
        frame_wh = data.get("frame_wh") or frame_wh
        if start is not None:
            first = start if first is None or start < first else first
            last = start if last is None or start > last else last

    for data, _count, start in rows:
        seen_starts.add(start.timestamp() if start else None)
        add(data, start)
    live = get_engine().heatmap_open_bucket(str(camera_id))
    if live and live.get("start_ts") is not None and live["start_ts"] not in seen_starts:
        start = datetime.fromtimestamp(float(live["start_ts"]), tz=timezone.utc)
        if start >= since - timedelta(seconds=float(live.get("bucket_s") or 60)):
            add(live, start)

    total = sum(values)
    base = {"camera_id": camera_id, "minutes": minutes, "grid": [gw, gh], "unit": "person_seconds"}
    if total <= 0:
        return {**base, "status": "no_data", "values": [], "max": None, "total": 0.0, "buckets": 0}
    return {
        **base,
        "status": "ok",
        "values": [round(v, 2) for v in values],
        "max": round(max(values), 2),
        "total": round(total, 2),
        "buckets": buckets,
        "frame_wh": frame_wh,
        "from": first.isoformat() if first else None,
        "to": now.isoformat(),
    }
