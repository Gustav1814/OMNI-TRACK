"""
OmniTrack AI - zone analytics API (Remediation Plan, Phase 3).

Every number comes from either the live ZoneAnalyticsEngine (current counts,
levels, queue lengths, fire state) or from events it persisted (visits, waits,
services, samples, incidents, emotion buckets, vibe samples). Nothing is
estimated or filled in: with nothing measured an endpoint returns its usual
shape with empty lists / null values and "status": "no_data".

Definitions live in app/analytics/definitions.py and are echoed in each
response under "definitions" so the UI can show what a number means.
"""

from __future__ import annotations

import os
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, desc, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import definitions as d
from app.analytics.engine import (
    CAMERA_STALE_S,
    LONG_QUEUE_HOLD_S,
    LONG_QUEUE_LENGTH,
    OVERCROWD_HOLD_S,
    SAMPLE_INTERVAL_S,
    VIBE_INTERVAL_S,
    get_engine,
)
from app.config import settings
from app.database import get_db
from app.ai.demographics import demographics_installed
from app.ai.demographics import summarize as demographics_summary
from app.models.analytics import DemographicSnapshot, StoreVibeScore
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.user import User
from app.models.zones import EmotionAggregate, FireIncident, TrackVisit, Zone, ZoneEvent, ZoneSample
from app.routers.zones import fire_incident_dict
from app.security.dependencies import get_current_user

NO_DATA = "no_data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def store_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.STORE_TIMEZONE or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _tz_name() -> str:
    return store_tz().key


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_bounds(day: Optional[date_cls] = None) -> Tuple[datetime, datetime]:
    tz = store_tz()
    day = day or _now().astimezone(tz).date()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _window(hours: Optional[int]) -> Tuple[datetime, datetime, str]:
    """[start, end) for `hours` back from now, or today (store time zone)."""
    now = _now()
    if hours:
        return now - timedelta(hours=hours), now, f"last_{hours}h"
    start, _ = _day_bounds()
    return start, now, "today"


def _window_out(start: datetime, end: datetime, label: str) -> dict:
    return {"label": label, "start": start.isoformat(), "end": end.isoformat(), "timezone": _tz_name()}


def _r(v: Optional[float], nd: int = 1) -> Optional[float]:
    return None if v is None else round(float(v), nd)


async def _zones(db: AsyncSession, types: Tuple[str, ...], camera_id: Optional[int] = None) -> List[Zone]:
    q = select(Zone).where(Zone.zone_type.in_(types)).order_by(Zone.camera_id, Zone.id)
    if camera_id is not None:
        q = q.where(Zone.camera_id == camera_id)
    return list((await db.execute(q)).scalars().all())


async def _observed_seconds(db: AsyncSession, zone_ids: List[int], start: datetime, end: datetime) -> Dict[int, float]:
    """Seconds each zone was actually watched: 5 s samples x interval."""
    if not zone_ids:
        return {}
    rows = (
        await db.execute(
            select(ZoneSample.zone_id, func.count(ZoneSample.id))
            .where(ZoneSample.zone_id.in_(zone_ids), ZoneSample.ts >= start, ZoneSample.ts < end)
            .group_by(ZoneSample.zone_id)
        )
    ).all()
    return {zid: n * SAMPLE_INTERVAL_S for zid, n in rows}


def _pct(q: float, col):
    return func.percentile_cont(q).within_group(col.asc())


def _live_cameras() -> List[dict]:
    engine = get_engine()
    now = _now().timestamp()
    out = []
    for cid in engine.camera_ids():
        snap = engine.snapshot(cid)
        if snap.get("ts") is not None and now - float(snap["ts"]) <= CAMERA_STALE_S:
            out.append(snap)
    return out


def _live_zone_index() -> Dict[Any, dict]:
    idx: Dict[Any, dict] = {}
    for cam in _live_cameras():
        for z in cam["zones"]:
            idx[z["id"]] = {**z, "camera_id": cam["camera_id"]}
    return idx


# ---------------------------------------------------------------------------
# Shelf
# ---------------------------------------------------------------------------

shelf_router = APIRouter(prefix="/api/shelf", tags=["Shelf Analytics"])

SHELF_DEFINITIONS = {
    "membership": "A person is in a zone when the bottom-centre of their box (foot point) is inside the polygon.",
    "visit": f"Inside for at least {d.DWELL_START_S:g} s; ends after {d.DWELL_END_S:g} s outside.",
    "dwell": "Time from entering to the last moment inside the zone.",
    "engaged_visit": f"A visit with dwell over {d.ENGAGED_DWELL_S:g} s.",
    "stop_rate": "Visits / people who entered the zone (passers-by).",
    "attention_score": "Engaged visits x median dwell, scaled so the top zone = 100.",
}


async def shelf_metrics(db: AsyncSession, start: datetime, end: datetime, camera_id: Optional[int] = None) -> List[dict]:
    zones = await _zones(db, ("shelf",), camera_id)
    if not zones:
        return []
    ids = [z.id for z in zones]
    is_visit = ZoneEvent.event_type == "zone_visit"
    rows = (
        await db.execute(
            select(
                ZoneEvent.zone_id,
                func.count().filter(ZoneEvent.event_type == "zone_pass"),
                func.count().filter(is_visit),
                func.count().filter(and_(is_visit, ZoneEvent.duration_s > d.ENGAGED_DWELL_S)),
                _pct(0.5, ZoneEvent.duration_s).filter(is_visit),
                _pct(0.9, ZoneEvent.duration_s).filter(is_visit),
                func.avg(ZoneEvent.duration_s).filter(is_visit),
            )
            .where(
                ZoneEvent.zone_id.in_(ids),
                ZoneEvent.event_type.in_(("zone_visit", "zone_pass")),
                ZoneEvent.ts >= start,
                ZoneEvent.ts < end,
            )
            .group_by(ZoneEvent.zone_id)
        )
    ).all()
    agg = {r[0]: r[1:] for r in rows}
    observed = await _observed_seconds(db, ids, start, end)
    live = _live_zone_index()
    out = []
    for z in zones:
        passes, visits, engaged, med, p90, mean = agg.get(z.id, (0, 0, 0, None, None, None))
        measured = z.id in agg or observed.get(z.id, 0) > 0
        passers = passes + visits
        lz = live.get(z.id)
        out.append({
            "zone_id": z.id, "zone_name": z.name, "camera_id": z.camera_id,
            "measured": measured,
            "observed_s": observed.get(z.id, 0.0),
            "passers_by": passers if measured else None,
            "visits": visits if measured else None,
            "engaged_visits": engaged if measured else None,
            "stop_rate": round(visits / passers, 3) if passers else None,
            "engagement_rate": round(engaged / visits, 3) if visits else None,
            "median_dwell_s": _r(med), "p90_dwell_s": _r(p90), "avg_dwell_s": _r(mean),
            "present_now": lz["present"] if lz else None,
        })
    scores = d.attention_scores({o["zone_id"]: (o["engaged_visits"] or 0, o["median_dwell_s"]) for o in out})
    for o in out:
        o["attention_score"] = scores.get(o["zone_id"]) if o["measured"] else None
    out.sort(key=lambda o: (o["attention_score"] or 0, o["visits"] or 0), reverse=True)
    return out


@shelf_router.get("/engagement")
async def get_shelf_engagement(
    hours: Optional[int] = Query(None, ge=1, le=24 * 31),
    camera_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    start, end, label = _window(hours)
    zones = await shelf_metrics(db, start, end, camera_id)
    measured = [z for z in zones if z["measured"]]
    return {
        "status": "ok" if measured else NO_DATA,
        "window": _window_out(start, end, label),
        "zones": zones,
        "totals": {
            "visits": sum(z["visits"] or 0 for z in measured),
            "engaged_visits": sum(z["engaged_visits"] or 0 for z in measured),
            "passers_by": sum(z["passers_by"] or 0 for z in measured),
        } if measured else None,
        "configured_zones": len(zones),
        "definitions": SHELF_DEFINITIONS,
    }


@shelf_router.get("/top-zones")
async def get_top_zones(
    limit: int = Query(5, ge=1, le=50),
    hours: Optional[int] = Query(None, ge=1, le=24 * 31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    start, end, label = _window(hours)
    zones = [z for z in await shelf_metrics(db, start, end) if z["measured"] and z["visits"]]
    return {"status": "ok" if zones else NO_DATA, "window": _window_out(start, end, label), "zones": zones[:limit]}


# ---------------------------------------------------------------------------
# Crowd
# ---------------------------------------------------------------------------

crowd_router = APIRouter(prefix="/api/crowd", tags=["Crowd Density"])

CROWD_DEFINITIONS = {
    "count": f"People whose foot point is inside the zone, median over {d.CROWD_SMOOTHING_S:g} s.",
    "levels": "empty < 10 %, low 10-30 %, moderate 30-60 %, high 60-90 %, critical >= 90 % of capacity.",
    "hysteresis": "A level is left only when occupancy drops max(5 %, 1 person) below its threshold.",
    "overcrowding_alert": f"Critical for {OVERCROWD_HOLD_S:g} s; closes after 30 s below critical.",
}


@crowd_router.get("/status")
async def get_crowd_status(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    zones = await _zones(db, ("crowd",))
    live = _live_zone_index()
    cams = _live_cameras()
    items = []
    for z in zones:
        lz = live.get(z.id)
        items.append({
            "zone_id": z.id, "zone_name": z.name, "camera_id": z.camera_id, "capacity": z.capacity,
            "live": lz is not None,
            "count": lz["count"] if lz else None,
            "raw_count": lz["raw_count"] if lz else None,
            "occupancy_pct": lz.get("pct") if lz else None,
            "level": lz.get("level") if lz else None,
            "alert_open": lz["alert_open"] if lz else False,
        })
    occupancy = [c["occupancy"] for c in cams if c.get("occupancy") is not None]
    return {
        "status": "ok" if any(i["live"] for i in items) or occupancy else NO_DATA,
        "zones": items,
        "store_occupancy": sum(occupancy) if occupancy else None,
        "cameras_live": len(cams),
        "configured_zones": len(zones),
        "definitions": CROWD_DEFINITIONS,
    }


def _bucket_seconds(hours: float) -> int:
    if hours <= 2:
        return 60
    if hours <= 24:
        return 300
    return 3600


@crowd_router.get("/history")
async def crowd_history(
    zone_id: Optional[int] = None,
    hours: int = Query(6, ge=1, le=24 * 31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Smoothed count per time bucket for one zone, or whole-store occupancy."""
    end = _now()
    start = end - timedelta(hours=hours)
    bucket = _bucket_seconds(hours)
    b = func.date_bin(timedelta(seconds=bucket), ZoneSample.ts, literal(datetime(2000, 1, 1, tzinfo=timezone.utc)))
    if zone_id is not None:
        zone = await db.get(Zone, zone_id)
        if zone is None:
            raise HTTPException(status_code=404, detail="Zone not found")
        q = (
            select(b.label("b"), func.avg(ZoneSample.count), func.max(ZoneSample.count), func.max(ZoneSample.capacity))
            .where(ZoneSample.zone_id == zone_id, ZoneSample.ts >= start)
            .group_by("b").order_by("b")
        )
        rows = (await db.execute(q)).all()
        series = [{"ts": r[0].isoformat(), "avg": _r(r[1], 2), "max": _r(r[2], 1),
                   "occupancy_pct": round(float(r[1]) / r[3], 4) if r[3] else None} for r in rows]
        name = zone.name
    else:
        # Store occupancy: per camera average within the bucket, summed over cameras.
        per_cam = (
            select(b.label("b"), ZoneSample.camera_id, func.avg(ZoneSample.count).label("c"), func.max(ZoneSample.count).label("m"))
            .where(ZoneSample.zone_id.is_(None), ZoneSample.ts >= start)
            .group_by("b", ZoneSample.camera_id)
        ).subquery()
        rows = (await db.execute(
            select(per_cam.c.b, func.sum(per_cam.c.c), func.sum(per_cam.c.m)).group_by(per_cam.c.b).order_by(per_cam.c.b)
        )).all()
        series = [{"ts": r[0].isoformat(), "avg": _r(r[1], 2), "max": _r(r[2], 1), "occupancy_pct": None} for r in rows]
        name = "Whole store"
    return {"status": "ok" if series else NO_DATA, "zone_id": zone_id, "zone_name": name,
            "bucket_s": bucket, "window_hours": hours, "series": series}


# ---------------------------------------------------------------------------
# Checkout (queue + counter lanes)
# ---------------------------------------------------------------------------

checkout_router = APIRouter(prefix="/api/checkout", tags=["Checkout Analytics"])

CHECKOUT_DEFINITIONS = {
    "queue_length": f"People in the queue polygon, median over {d.QUEUE_SMOOTHING_S:g} s.",
    "wait": f"Queue entry to counter entry (same lane; up to {d.QUEUE_TO_COUNTER_GRACE_S:g} s between them).",
    "service_time": f"Time at the counter (visits >= {d.DWELL_START_S:g} s; > {d.SERVICE_MAX_S / 60:g} min counts as staff).",
    "throughput": "Counter exits per hour of observed time.",
    "abandonment": f"Left the queue after >= {d.QUEUE_MIN_PRESENCE_S:g} s without reaching the counter.",
    "long_queue_alert": f"Queue length >= {LONG_QUEUE_LENGTH} for {LONG_QUEUE_HOLD_S / 60:g} min.",
}


async def checkout_lanes(db: AsyncSession, start: datetime, end: datetime) -> List[dict]:
    zones = await _zones(db, ("queue", "counter"))
    if not zones:
        return []
    lanes: Dict[Tuple[int, str], dict] = {}
    for z in zones:
        key = (z.camera_id, (z.lane or "default"))
        lane = lanes.setdefault(key, {"camera_id": z.camera_id, "lane": key[1], "queue_zone": None, "counter_zone": None})
        lane["queue_zone" if z.zone_type == "queue" else "counter_zone"] = {"id": z.id, "name": z.name}
    ids = [z.id for z in zones]
    rows = (
        await db.execute(
            select(
                ZoneEvent.zone_id, ZoneEvent.event_type, func.count(),
                _pct(0.5, ZoneEvent.duration_s), _pct(0.9, ZoneEvent.duration_s), func.avg(ZoneEvent.duration_s),
            )
            .where(
                ZoneEvent.zone_id.in_(ids),
                ZoneEvent.event_type.in_(("queue_wait", "queue_abandon", "service")),
                ZoneEvent.ts >= start, ZoneEvent.ts < end,
            )
            .group_by(ZoneEvent.zone_id, ZoneEvent.event_type)
        )
    ).all()
    stats = {(r[0], r[1]): r[2:] for r in rows}
    observed = await _observed_seconds(db, ids, start, end)
    live = _live_zone_index()
    live_lanes: Dict[Tuple[str, str], dict] = {}
    for cam in _live_cameras():
        for ln in cam["lanes"]:
            live_lanes[(cam["camera_id"], ln["lane"])] = ln
    out = []
    for (cam_id, lane_name), lane in sorted(lanes.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        qz, cz = lane["queue_zone"], lane["counter_zone"]
        wait = stats.get((qz["id"], "queue_wait")) if qz else None
        aband = stats.get((qz["id"], "queue_abandon")) if qz else None
        serv = stats.get((cz["id"], "service")) if cz else None
        obs_s = observed.get(cz["id"], 0.0) if cz else 0.0
        n_wait = wait[0] if wait else 0
        n_ab = aband[0] if aband else 0
        n_serv = serv[0] if serv else 0
        measured = bool(wait or aband or serv or obs_s or (qz and observed.get(qz["id"])))
        ll = live_lanes.get((str(cam_id), lane_name))
        out.append({
            **lane,
            "paired": bool(qz and cz),
            "measured": measured,
            "observed_s": obs_s,
            "queue_length_now": ll["queue_length"] if ll else None,
            "waiting_now": ll["waiting"] if ll else None,
            "long_queue_alert": bool(qz and live.get(qz["id"], {}).get("alert_open")),
            "customers_waited": n_wait if measured else None,
            "median_wait_s": _r(wait[1]) if wait else None,
            "p90_wait_s": _r(wait[2]) if wait else None,
            "served": n_serv if measured else None,
            "median_service_s": _r(serv[1]) if serv else None,
            "p90_service_s": _r(serv[2]) if serv else None,
            "throughput_per_hour": round(n_serv / (obs_s / 3600.0), 1) if obs_s >= 60 else None,
            "abandoned": n_ab if measured else None,
            "abandonment_rate": round(n_ab / (n_ab + n_wait), 3) if (n_ab + n_wait) else None,
        })
    return out


@checkout_router.get("/metrics")
async def get_checkout_metrics(
    hours: Optional[int] = Query(None, ge=1, le=24 * 31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    start, end, label = _window(hours)
    lanes = await checkout_lanes(db, start, end)
    return {"status": "ok" if any(ln["measured"] or ln["queue_length_now"] is not None for ln in lanes) else NO_DATA,
            "window": _window_out(start, end, label), "lanes": lanes, "definitions": CHECKOUT_DEFINITIONS}


@checkout_router.get("/summary")
async def checkout_summary(
    hours: Optional[int] = Query(None, ge=1, le=24 * 31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Store-wide wait/service medians over all individual events."""
    start, end, label = _window(hours)
    rows = (
        await db.execute(
            select(ZoneEvent.event_type, func.count(), _pct(0.5, ZoneEvent.duration_s), _pct(0.9, ZoneEvent.duration_s))
            .where(ZoneEvent.event_type.in_(("queue_wait", "queue_abandon", "service")),
                   ZoneEvent.ts >= start, ZoneEvent.ts < end)
            .group_by(ZoneEvent.event_type)
        )
    ).all()
    s = {r[0]: r[1:] for r in rows}
    w, a, sv = s.get("queue_wait"), s.get("queue_abandon"), s.get("service")
    lanes_live = [ln for c in _live_cameras() for ln in c["lanes"]]
    qnow = [ln["queue_length"] for ln in lanes_live if ln["queue_length"] is not None]
    n_w, n_a = (w[0] if w else 0), (a[0] if a else 0)
    return {
        "status": "ok" if (rows or qnow) else NO_DATA,
        "window": _window_out(start, end, label),
        "customers_waited": n_w if rows else None,
        "median_wait_s": _r(w[1]) if w else None,
        "p90_wait_s": _r(w[2]) if w else None,
        "served": (sv[0] if sv else 0) if rows else None,
        "median_service_s": _r(sv[1]) if sv else None,
        "abandoned": n_a if rows else None,
        "abandonment_rate": round(n_a / (n_a + n_w), 3) if (n_a + n_w) else None,
        "queue_length_now": sum(qnow) if qnow else None,
        "lanes_live": len(lanes_live),
    }


# ---------------------------------------------------------------------------
# Fire
# ---------------------------------------------------------------------------

fire_router = APIRouter(prefix="/api/fire", tags=["Fire & Smoke Detection"])

FIRE_DEFINITIONS = {
    "alert": f"{d.FIRE_HITS_REQUIRED} of the last {d.FIRE_WINDOW} fire inferences have a box with confidence >= "
             f"{d.FIRE_MIN_CONF:g} and area >= {d.FIRE_MIN_AREA_FRAC * 100:g} % of the frame.",
    "clear": f"No qualifying box for {d.FIRE_CLEAR_S:g} s.",
    "cooldown": f"A re-trigger within {d.FIRE_COOLDOWN_S / 60:g} min of clearing re-opens the same incident.",
}


@fire_router.get("/alerts")
async def get_fire_alerts(
    hours: int = Query(24, ge=1, le=24 * 31),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    since = _now() - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(FireIncident).where(FireIncident.started_at >= since).order_by(desc(FireIncident.started_at)).limit(limit)
        )
    ).scalars().all()
    return {"status": "ok" if rows else NO_DATA, "window_hours": hours,
            "incidents": [fire_incident_dict(f) for f in rows], "definitions": FIRE_DEFINITIONS}


def _fire_model_present() -> bool:
    path = getattr(settings, "FIRE_MODEL_PATH", None)
    return bool(path) and os.path.exists(str(path))


@fire_router.get("/status")
async def fire_status(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    cams = _live_cameras()
    per_cam = [{"camera_id": c["camera_id"], **c["fire"]} for c in cams]
    start, _end = _day_bounds()
    today = (await db.execute(select(func.count(FireIncident.id)).where(FireIncident.started_at >= start))).scalar() or 0
    active = [c for c in per_cam if c.get("active")]
    return {
        "status": "ok" if cams else NO_DATA,
        "active": bool(active),
        "active_cameras": [c["camera_id"] for c in active],
        "cameras": per_cam,
        "incidents_today": int(today),
        "model_present": _fire_model_present(),
        "definitions": FIRE_DEFINITIONS,
    }


# ---------------------------------------------------------------------------
# Emotion
# ---------------------------------------------------------------------------

emotion_router = APIRouter(prefix="/api/emotion", tags=["Emotion Recognition"])

EMOTION_DEFINITIONS = {
    "source": "Face crops classified by the HSEmotion model (8 AffectNet labels); only real classifications are counted.",
    "aggregation": f"Counts per zone per {d.EMOTION_BUCKET_S // 60} min; a face belongs to the zone of the person whose box contains it.",
    "sentiment": "Mean valence: happy +1; neutral, surprise 0; sad, angry, fear, disgust, contempt -1.",
}


def _emotion_model_present() -> bool:
    path = getattr(settings, "EMOTION_MODEL_PATH", None)
    return bool(path) and os.path.exists(str(path))


def _merge_counts(rows) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for counts in rows:
        for k, v in (counts or {}).items():
            out[k] = out.get(k, 0) + int(v)
    return out


@emotion_router.get("/current")
async def get_current_emotions(
    minutes: int = Query(30, ge=5, le=24 * 60),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    since = _now() - timedelta(minutes=minutes)
    rows = (
        await db.execute(
            select(EmotionAggregate, Zone.name)
            .join(Zone, Zone.id == EmotionAggregate.zone_id, isouter=True)
            .where(EmotionAggregate.bucket_start >= since - timedelta(seconds=d.EMOTION_BUCKET_S))
        )
    ).all()
    groups: Dict[Tuple[int, Optional[int]], dict] = {}
    for agg, zname in rows:
        g = groups.setdefault((agg.camera_id, agg.zone_id), {"camera_id": agg.camera_id, "zone_id": agg.zone_id,
                                                              "zone_name": zname or "Whole camera", "counts": []})
        g["counts"].append(agg.counts)
    zones = []
    for g in groups.values():
        counts = _merge_counts(g.pop("counts"))
        total = sum(counts.values())
        if total == 0:
            continue
        zones.append({**g, "counts": counts, "total": total,
                      "distribution": {k: round(v / total, 3) for k, v in counts.items()},
                      "dominant": d.dominant_label(counts), "sentiment": d.sentiment_from_counts(counts)})
    zones.sort(key=lambda z: (z["zone_id"] is not None, -z["total"]))
    return {"status": "ok" if zones else NO_DATA, "window_minutes": minutes, "zones": zones,
            "model_present": _emotion_model_present(), "definitions": EMOTION_DEFINITIONS}


@emotion_router.get("/store-sentiment")
async def store_sentiment(
    hours: int = Query(6, ge=1, le=24 * 7),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Store-wide (camera-level rows) sentiment per 5-min bucket."""
    since = _now() - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(EmotionAggregate.bucket_start, EmotionAggregate.counts)
            .where(EmotionAggregate.zone_id.is_(None), EmotionAggregate.bucket_start >= since)
            .order_by(EmotionAggregate.bucket_start)
        )
    ).all()
    by_bucket: Dict[datetime, List[dict]] = {}
    for b, counts in rows:
        by_bucket.setdefault(b, []).append(counts)
    timeline = []
    for b, lst in sorted(by_bucket.items()):
        c = _merge_counts(lst)
        timeline.append({"ts": b.isoformat(), "total": sum(c.values()), "sentiment": d.sentiment_from_counts(c),
                         "dominant": d.dominant_label(c), "counts": c})
    overall = _merge_counts([t["counts"] for t in timeline])
    total = sum(overall.values())
    return {
        "status": "ok" if total else NO_DATA,
        "window_hours": hours,
        "total_faces": total,
        "sentiment": d.sentiment_from_counts(overall),
        "dominant": d.dominant_label(overall),
        "counts": overall,
        "timeline": timeline,
        "model_present": _emotion_model_present(),
    }


# ---------------------------------------------------------------------------
# Store vibe
# ---------------------------------------------------------------------------

vibe_router = APIRouter(prefix="/api/vibe", tags=["Store Vibe"])

VIBE_FORMULA = {
    "formula": "vibe = 100 x sum(w_i x c_i) / sum(w_i) over the measured components c_i in [0, 1]",
    "weights": d.VIBE_WEIGHTS,
    "components": {
        "occupancy": "Smoothed people in crowd zones / their total capacity (capped at 1).",
        "flow": f"Arrivals per minute over 5 min / {d.VIBE_FLOW_REF_PER_MIN:g} (capped at 1); arrivals are inward "
                "entry-line crossings, or new tracks when no entry line is drawn.",
        "engagement": "Engaged visits / visits in shelf zones over 15 min.",
        "sentiment": "(mean face valence + 1) / 2 over 15 min; only with the emotion model running.",
    },
    "labels": {label: f"< {upper:g}" for upper, label in d.VIBE_LABELS},
    "missing": "A component with no measurement is left out and the other weights are renormalised.",
}


def _vibe_from_row(row: StoreVibeScore) -> Optional[dict]:
    b = row.breakdown or {}
    if not isinstance(b, dict) or not b.get("components"):
        return None  # rows written before the documented formula existed
    return {"score": row.overall_score, "label": row.vibe_label, "components": b.get("components"),
            "weights_used": b.get("weights_used"), "inputs": b.get("inputs"),
            "ts": row.timestamp.timestamp() if row.timestamp else None}


async def current_vibe(db: AsyncSession) -> Tuple[Optional[dict], str]:
    v = get_engine().latest_vibe()
    now = _now().timestamp()
    if v and now - float(v.get("ts") or 0) <= 3 * VIBE_INTERVAL_S:
        return v, "live"
    row = (
        await db.execute(
            select(StoreVibeScore).where(StoreVibeScore.timestamp >= _now() - timedelta(minutes=15))
            .order_by(desc(StoreVibeScore.timestamp)).limit(5)
        )
    ).scalars().all()
    for r in row:
        rv = _vibe_from_row(r)
        if rv:
            return rv, "recent"
    return None, NO_DATA


@vibe_router.get("/current")
async def get_current_vibe(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    vibe, source = await current_vibe(db)
    out = {"status": "ok" if vibe else NO_DATA, "source": source, "formula": VIBE_FORMULA}
    if vibe:
        out.update(score=vibe["score"], label=vibe["label"], components=vibe["components"],
                   weights_used=vibe.get("weights_used"), inputs=vibe.get("inputs"),
                   ts=datetime.fromtimestamp(vibe["ts"], tz=timezone.utc).isoformat() if vibe.get("ts") else None)
    else:
        out.update(score=None, label=None, components={}, weights_used=None, inputs=None, ts=None)
    return out


@vibe_router.get("/trend")
async def vibe_trend(
    hours: int = Query(24, ge=1, le=24 * 31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(StoreVibeScore).where(StoreVibeScore.timestamp >= _now() - timedelta(hours=hours))
            .order_by(StoreVibeScore.timestamp)
        )
    ).scalars().all()
    series = []
    for r in rows:
        v = _vibe_from_row(r)
        if v:
            comps = v["components"] or {}
            series.append({"ts": r.timestamp.isoformat(), "score": v["score"], "label": v["label"],
                           **{k: round(c["normalized"] * 100, 1) for k, c in comps.items()}})
    return {"status": "ok" if series else NO_DATA, "window_hours": hours, "series": series}


# ---------------------------------------------------------------------------
# Demographics: one DemographicSnapshot row per finished visit (app/ai/demographics.py)
# ---------------------------------------------------------------------------

demographics_router = APIRouter(prefix="/api/demographics", tags=["Demographics"])


@demographics_router.get("/current")
async def get_demographics(
    zone: Optional[str] = None,
    hours: int = Query(24, ge=1, le=24 * 31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Age-band / gender shares over finished visits in the window.

    status: ok | insufficient_samples (fewer than DEMOGRAPHICS_MIN_VISITS attributed visits;
    percentages hidden) | no_data | model_not_installed. `unattributed` = finished visits in the
    window without a demographic reading (face never seen / too small / ambiguous)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = select(DemographicSnapshot.age_band, DemographicSnapshot.gender).where(
        DemographicSnapshot.timestamp >= since, DemographicSnapshot.age_band.is_not(None)
    )
    vq = select(func.count()).select_from(TrackVisit).where(TrackVisit.last_seen >= since)
    if zone:
        q = q.where(DemographicSnapshot.zone == zone)
        vq = vq.where(TrackVisit.camera_id.in_(select(Camera.id).where(Camera.zone == zone)))
    rows = (await db.execute(q)).all()
    total_visits = int((await db.execute(vq)).scalar() or 0)
    out = demographics_summary(rows, total_visits=max(total_visits, len(rows)), installed=demographics_installed())
    out.update(zone=zone, window_hours=hours)
    # legacy keys read by the current dashboard
    out["age_distribution"] = out["age_bands"]
    out["gender_distribution"] = out["gender"]
    out["total_count"] = out["sample_count"]
    return out


# ---------------------------------------------------------------------------
# Peak hours / footfall
# ---------------------------------------------------------------------------

peak_hours_router = APIRouter(prefix="/api/peak-hours", tags=["Peak Hours"])

TRAFFIC_DEFINITIONS = {
    "footfall": "People crossing an entry line inward (when an entry line is drawn).",
    "visitors": "Distinct people seen: one per Re-ID global id, else one per confirmed track lasting >= 1 s.",
    "observed": "An hour counts as observed when at least one camera was processing during it.",
}


def _visitor_count_expr():
    # distinct global ids + tracks without one
    return func.count(func.distinct(TrackVisit.global_id)) + func.count().filter(TrackVisit.global_id.is_(None))


async def hourly_traffic(db: AsyncSession, start: datetime, end: datetime) -> Tuple[List[dict], bool]:
    tzname = _tz_name()
    hour_tv = func.extract("hour", func.timezone(tzname, TrackVisit.first_seen))
    visitors = dict((int(h), int(n)) for h, n in (await db.execute(
        select(hour_tv, _visitor_count_expr())
        .where(TrackVisit.first_seen >= start, TrackVisit.first_seen < end).group_by(hour_tv)
    )).all())
    hour_ev = func.extract("hour", func.timezone(tzname, ZoneEvent.ts))
    entries = dict((int(h), int(n)) for h, n in (await db.execute(
        select(hour_ev, func.count())
        .where(ZoneEvent.event_type == "line_cross", ZoneEvent.value > 0, ZoneEvent.ts >= start, ZoneEvent.ts < end)
        .group_by(hour_ev)
    )).all())
    hour_s = func.extract("hour", func.timezone(tzname, ZoneSample.ts))
    observed = dict((int(h), int(n)) for h, n in (await db.execute(
        select(hour_s, func.count(func.distinct(ZoneSample.camera_id)))
        .where(ZoneSample.zone_id.is_(None), ZoneSample.ts >= start, ZoneSample.ts < end).group_by(hour_s)
    )).all())
    has_line = bool((await db.execute(select(func.count(Zone.id)).where(Zone.zone_type == "entry_line"))).scalar())
    hours = []
    for h in range(24):
        seen = h in observed or h in visitors or h in entries
        hours.append({
            "hour": h,
            "observed": seen,
            "visitors": visitors.get(h, 0) if seen else None,
            "entries": (entries.get(h, 0) if seen else None) if has_line else None,
        })
    return hours, has_line


@peak_hours_router.get("/today")
async def get_peak_hours(
    day: Optional[date_cls] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    start, end = _day_bounds(day)
    hours, has_line = await hourly_traffic(db, start, min(end, _now()))
    metric = "entries" if has_line else "visitors"
    observed = [h for h in hours if h["observed"]]
    peak = max(observed, key=lambda h: h[metric] or 0) if observed else None
    total_visitors = (await db.execute(
        select(_visitor_count_expr()).where(TrackVisit.first_seen >= start, TrackVisit.first_seen < end)
    )).scalar() or 0
    return {
        "status": "ok" if observed else NO_DATA,
        "date": start.astimezone(store_tz()).date().isoformat(),
        "timezone": _tz_name(),
        "metric": metric,
        "peak_hour": peak["hour"] if peak and (peak[metric] or 0) > 0 else None,
        "peak_count": peak[metric] if peak and (peak[metric] or 0) > 0 else None,
        "total_visitors": int(total_visitors) if observed else None,
        "total_entries": sum(h["entries"] or 0 for h in observed) if (observed and has_line) else None,
        "observed_hours": len(observed),
        "hourly": hours,
        "definitions": TRAFFIC_DEFINITIONS,
    }


@peak_hours_router.get("/weekly")
async def weekly_heatmap(
    days: int = Query(7, ge=1, le=31),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Visitors per (weekday, hour) over the last `days` days, store time zone."""
    end = _now()
    start = end - timedelta(days=days)
    tzname = _tz_name()
    local = func.timezone(tzname, TrackVisit.first_seen)
    dow = func.extract("isodow", local)
    hr = func.extract("hour", local)
    rows = (await db.execute(
        select(dow, hr, _visitor_count_expr())
        .where(TrackVisit.first_seen >= start).group_by(dow, hr)
    )).all()
    cells = [{"weekday": int(w), "hour": int(h), "visitors": int(n)} for w, h, n in rows]
    return {"status": "ok" if cells else NO_DATA, "days": days, "timezone": tzname, "cells": cells}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

dashboard_router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


async def _visitors_between(db: AsyncSession, start: datetime, end: datetime) -> int:
    return int((await db.execute(
        select(_visitor_count_expr()).where(TrackVisit.first_seen >= start, TrackVisit.first_seen < end)
    )).scalar() or 0)


async def _entries_between(db: AsyncSession, start: datetime, end: datetime) -> int:
    return int((await db.execute(
        select(func.count(ZoneEvent.id)).where(
            ZoneEvent.event_type == "line_cross", ZoneEvent.value > 0, ZoneEvent.ts >= start, ZoneEvent.ts < end)
    )).scalar() or 0)


async def _observed_between(db: AsyncSession, start: datetime, end: datetime) -> int:
    return int((await db.execute(
        select(func.count(ZoneSample.id)).where(ZoneSample.zone_id.is_(None), ZoneSample.ts >= start, ZoneSample.ts < end)
    )).scalar() or 0)


def _delta(today: Optional[float], yesterday: Optional[float]) -> Optional[float]:
    if today is None or not yesterday:
        return None
    return round((today - yesterday) / yesterday * 100.0, 1)


@dashboard_router.get("/overview")
async def get_dashboard_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    now = _now()
    start, _end = _day_bounds()
    y_start, y_now = start - timedelta(days=1), now - timedelta(days=1)

    total_cameras = int((await db.execute(select(func.count(Camera.id)))).scalar() or 0)
    cams = _live_cameras()
    pipeline = getattr(request.app.state, "pipeline", None)
    streaming = None
    try:
        streaming = int(pipeline.stream_manager.active_count) if pipeline is not None else None
    except Exception:
        streaming = None

    observed_today = await _observed_between(db, start, now)
    observed_yday = await _observed_between(db, y_start, y_now)
    has_line = bool((await db.execute(select(func.count(Zone.id)).where(Zone.zone_type == "entry_line"))).scalar())

    visitors = await _visitors_between(db, start, now) if observed_today else None
    visitors_y = await _visitors_between(db, y_start, y_now) if observed_yday else None
    entries = await _entries_between(db, start, now) if (observed_today and has_line) else None
    entries_y = await _entries_between(db, y_start, y_now) if (observed_yday and has_line) else None

    det_today = int((await db.execute(select(func.count(Detection.id)).where(Detection.timestamp >= start))).scalar() or 0)

    med_visit = (await db.execute(
        select(_pct(0.5, TrackVisit.duration_s)).where(TrackVisit.first_seen >= start)
    )).scalar()

    fires_today = int((await db.execute(select(func.count(FireIncident.id)).where(FireIncident.started_at >= start))).scalar() or 0)
    active_fire = any(c["fire"]["active"] for c in cams)

    open_alerts = []
    for c in cams:
        for z in c["zones"]:
            if z.get("alert_open"):
                open_alerts.append({"camera_id": c["camera_id"], "zone_id": z["id"], "zone_name": z["name"],
                                    "alert_type": "overcrowding" if z["zone_type"] == "crowd" else "long_queue"})
            if z["zone_type"] == "restricted" and z.get("dwell_alerts_open"):
                open_alerts.append({"camera_id": c["camera_id"], "zone_id": z["id"], "zone_name": z["name"],
                                    "alert_type": "intrusion", "people": z["dwell_alerts_open"]})
    if active_fire:
        for c in cams:
            if c["fire"]["active"]:
                open_alerts.append({"camera_id": c["camera_id"], "zone_id": None, "zone_name": None, "alert_type": "fire"})

    occ = [c["occupancy"] for c in cams if c.get("occupancy") is not None]
    wait_med = (await db.execute(
        select(_pct(0.5, ZoneEvent.duration_s)).where(ZoneEvent.event_type == "queue_wait", ZoneEvent.ts >= start)
    )).scalar()

    hours, _hl = await hourly_traffic(db, start, now)
    metric = "entries" if has_line else "visitors"
    observed_hours = [h for h in hours if h["observed"]]
    peak = max(observed_hours, key=lambda h: h[metric] or 0) if observed_hours else None

    shelves = [z for z in await shelf_metrics(db, start, now) if z["measured"] and z["visits"]]
    vibe, vibe_source = await current_vibe(db)

    measured_anything = bool(observed_today or cams or det_today)
    return {
        "status": "ok" if measured_anything else NO_DATA,
        "as_of": now.isoformat(),
        "timezone": _tz_name(),
        "cameras": {"total": total_cameras, "streaming": streaming, "analysed_live": len(cams)},
        "current_occupancy": sum(occ) if occ else None,
        "visitors_today": visitors,
        "entries_today": entries,
        "footfall_metric": metric,
        "median_visit_s": _r(med_visit),
        "detections_today": det_today,
        "fire_incidents_today": fires_today,
        "active_fire": active_fire,
        "open_alerts": open_alerts,
        "median_wait_s": _r(wait_med),
        "peak_hour_today": peak["hour"] if peak and (peak[metric] or 0) > 0 else None,
        "hourly": hours,
        "top_zone": {"zone_id": shelves[0]["zone_id"], "zone_name": shelves[0]["zone_name"],
                     "attention_score": shelves[0]["attention_score"]} if shelves else None,
        "store_vibe": {"score": vibe["score"], "label": vibe["label"], "source": vibe_source} if vibe else None,
        "vs_yesterday": {
            k: v for k, v in {
                "visitors_today": _delta(visitors, visitors_y),
                "entries_today": _delta(entries, entries_y),
            }.items() if v is not None
        },
    }


@dashboard_router.get("/recent-events")
async def recent_events(
    limit: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Latest measured zone events (visits, waits, services, crossings, alerts)."""
    rows = (
        await db.execute(
            select(ZoneEvent, Zone.name, Zone.zone_type)
            .join(Zone, Zone.id == ZoneEvent.zone_id, isouter=True)
            .where(ZoneEvent.event_type.in_(("zone_visit", "queue_wait", "service", "queue_abandon",
                                             "line_cross", "crowd_level", "alert")))
            .order_by(desc(ZoneEvent.ts)).limit(limit)
        )
    ).all()
    events = [{
        "id": e.id, "type": e.event_type, "ts": e.ts.isoformat(), "camera_id": e.camera_id,
        "zone_id": e.zone_id, "zone_name": name, "zone_type": ztype, "track_id": e.track_id,
        "global_id": e.global_id, "duration_s": e.duration_s, "value": e.value,
        "payload": e.payload,
    } for e, name, ztype in rows]
    return {"status": "ok" if events else NO_DATA, "events": events}


__all__ = [
    "shelf_router", "crowd_router", "checkout_router", "fire_router", "emotion_router",
    "vibe_router", "demographics_router", "peak_hours_router", "dashboard_router",
]
