"""
Zone persistence: zone CRUD reads for the engine, and batched writes of the
events produced by ZoneAnalyticsEngine.

    await load_zones(camera_id)      -> list[dict] in the engine's zone format
    await persist_events(events)     -> one transaction per call

persist_events never raises: a failed batch is logged and dropped so a database
hiccup cannot stall the video pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, select

from app.database import AsyncSessionLocal
from app.models.analytics import FootTraffic, StoreVibeScore
from app.models.camera import Camera
from app.models.zones import (
    EmotionAggregate,
    FireIncident,
    TrackVisit,
    Zone,
    ZoneEvent,
    ZoneSample,
)

logger = logging.getLogger(__name__)

ZONE_EVENT_TYPES = {
    "zone_visit",
    "zone_pass",
    "queue_wait",
    "queue_abandon",
    "service",
    "line_cross",
    "crowd_level",
    "alert",
}


def zone_to_dict(z: Zone) -> dict:
    return {
        "id": z.id,
        "camera_id": z.camera_id,
        "name": z.name,
        "zone_type": z.zone_type,
        "points": z.points or [],
        "capacity": z.capacity,
        "lane": z.lane,
        "config": z.config or {},
        "color": z.color,
        "is_active": bool(z.is_active),
        "created_at": z.created_at.isoformat() if z.created_at else None,
        "updated_at": z.updated_at.isoformat() if z.updated_at else None,
    }


HEATMAP_ZONE = "__heatmap__"   # FootTraffic.zone marker for camera-wide spatial heatmap buckets


def _cam_int(camera_id: Any) -> Optional[int]:
    try:
        return int(camera_id)
    except (TypeError, ValueError):
        return None


def _dt(ts: Optional[float]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


async def load_zones(camera_id: str) -> list[dict]:
    """Active zones of a camera, in the format ZoneAnalyticsEngine.set_zones takes."""
    cam = _cam_int(camera_id)
    if cam is None:
        return []
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Zone).where(Zone.camera_id == cam, Zone.is_active.is_(True)).order_by(Zone.id)
            )
        ).scalars().all()
        return [zone_to_dict(z) for z in rows]


async def load_all_zones() -> Dict[int, List[dict]]:
    """Active zones of every camera, keyed by camera id."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(Zone).where(Zone.is_active.is_(True)).order_by(Zone.id))
        ).scalars().all()
    out: Dict[int, List[dict]] = {}
    for z in rows:
        out.setdefault(z.camera_id, []).append(zone_to_dict(z))
    return out


async def persist_events(events: list[dict]) -> None:
    """Write engine events in one transaction (best effort, never raises)."""
    if not events:
        return
    try:
        async with AsyncSessionLocal() as session:
            await _write(session, events)
            await session.commit()
    except Exception as exc:  # pragma: no cover - logged, pipeline continues
        logger.error("persist_events failed for %d events: %s", len(events), exc)


async def _write(session, events: Iterable[dict]) -> None:
    events = list(events)
    # Drop events for zones deleted since they were produced (FK safety).
    zone_ids = {e.get("zone_id") for e in events if e.get("zone_id") is not None}
    existing: set = set()
    if zone_ids:
        ints = {int(z) for z in zone_ids if _cam_int(z) is not None}
        if ints:
            existing = set((await session.execute(select(Zone.id).where(Zone.id.in_(ints)))).scalars().all())

    heat_cams: Dict[int, bool] = {}
    for e in events:
        etype = e.get("type")
        zid = e.get("zone_id")
        if zid is not None:
            zid = _cam_int(zid)
            if zid not in existing:
                continue
        payload = e.get("payload") or {}
        cam = _cam_int(e.get("camera_id"))

        if etype in ZONE_EVENT_TYPES:
            if cam is None:
                continue
            session.add(ZoneEvent(
                camera_id=cam, zone_id=zid, event_type=etype, ts=_dt(e["ts"]),
                track_id=e.get("track_id"), global_id=e.get("global_id"),
                duration_s=e.get("duration_s"), value=e.get("value"), payload=payload or None,
                event_uid=(str(e["event_uid"])[:36] if e.get("event_uid") else None),
            ))
        elif etype == "zone_sample":
            if cam is None or e.get("value") is None:
                continue
            session.add(ZoneSample(
                camera_id=cam, zone_id=zid, ts=_dt(e["ts"]), count=float(e["value"]),
                raw_count=payload.get("raw"), capacity=payload.get("capacity"),
                occupancy_pct=payload.get("pct"), level=payload.get("level"),
            ))
        elif etype == "track_visit":
            if cam is None:
                continue
            session.add(TrackVisit(
                camera_id=cam, track_id=int(e.get("track_id") or 0), global_id=e.get("global_id"),
                first_seen=_dt(payload.get("first_seen")), last_seen=_dt(payload.get("last_seen")),
                duration_s=float(e.get("duration_s") or 0.0),
            ))
        elif etype == "fire":
            if cam is None:
                continue
            await _upsert_fire(session, cam, payload)
        elif etype == "emotion_bucket":
            if cam is None:
                continue
            await _upsert_emotion(session, cam, zid, e, payload)
        elif etype == "heatmap_bucket":
            if cam is None or not payload.get("cells"):
                continue
            if cam not in heat_cams:
                heat_cams[cam] = (await session.get(Camera, cam)) is not None
            if not heat_cams[cam]:
                continue  # camera deleted since the bucket was produced (FK)
            start = float(e["ts"])
            end = payload.get("end_ts") or start + float(payload.get("bucket_s") or 60)
            session.add(FootTraffic(
                camera_id=cam, zone=HEATMAP_ZONE, person_count=int(payload.get("tracks") or 0),
                heatmap_data={k: payload.get(k) for k in ("grid", "cells", "frame_wh", "bucket_s")},
                timestamp=_dt(start), interval_start=_dt(start), interval_end=_dt(end),
            ))
        elif etype == "vibe_sample":
            comps = payload.get("components") or {}

            def _c(name: str) -> Optional[float]:
                c = comps.get(name)
                return round(c["normalized"] * 100.0, 1) if c else None

            session.add(StoreVibeScore(
                overall_score=float(payload["score"]),
                sentiment_score=_c("sentiment"),
                energy_score=_c("occupancy"),
                engagement_score=_c("engagement"),
                foot_traffic_score=_c("flow"),
                vibe_label=payload.get("label"),
                breakdown={k: payload.get(k) for k in ("components", "weights_used", "inputs")},
                timestamp=_dt(e["ts"]),
            ))
        if etype in ("fire", "emotion_bucket"):
            # Upserts look rows up by key; make rows added in this batch visible.
            await session.flush()


async def _upsert_fire(session, cam: int, p: dict) -> None:
    key = p.get("incident_key")
    if not key:
        return
    row = (await session.execute(select(FireIncident).where(FireIncident.incident_key == key))).scalar_one_or_none()
    if row is None:
        row = FireIncident(incident_key=key, camera_id=cam, started_at=_dt(p.get("started_ts")) or datetime.now(timezone.utc))
        session.add(row)
    action = p.get("action")
    row.status = "cleared" if action == "clear" else "active"
    row.last_hit_at = _dt(p.get("last_hit_ts"))
    row.cleared_at = _dt(p.get("cleared_ts")) if action == "clear" else None
    row.peak_conf = float(p.get("peak_conf") or 0.0)
    row.class_name = p.get("class_name")
    row.bbox = p.get("bbox_xywh")
    row.frame_wh = p.get("frame_wh")
    row.hits = int(p.get("hits") or 0)
    row.retriggers = int(p.get("retriggers") or 0)


async def _upsert_emotion(session, cam: int, zid: Optional[int], e: dict, p: dict) -> None:
    bstart = _dt(p.get("bucket_start", e.get("ts")))
    zone_clause = EmotionAggregate.zone_id.is_(None) if zid is None else EmotionAggregate.zone_id == zid
    row = (
        await session.execute(
            select(EmotionAggregate).where(
                and_(EmotionAggregate.camera_id == cam, zone_clause, EmotionAggregate.bucket_start == bstart)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = EmotionAggregate(camera_id=cam, zone_id=zid, bucket_start=bstart)
        session.add(row)
    # Engine counts are cumulative for the bucket, so the latest event wins.
    row.bucket_s = int(p.get("bucket_s") or 300)
    row.counts = dict(p.get("counts") or {})
    row.total = int(p.get("total") or 0)
    row.sentiment = e.get("value")
    row.dominant = p.get("dominant")
