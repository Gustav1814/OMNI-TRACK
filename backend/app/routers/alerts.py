"""
OmniTrack AI - Alerts centre.

One list over the three alert sources, newest first:
  zone_event    - analytics-engine zone alerts (zone_events.event_type="alert",
                  opening events only; the matching close event gives ended_at)
  fire_incident - fire/smoke incidents
  humanless     - humanless-store loss-prevention alerts

Reads need a logged-in user; acknowledging is admin-only (route policy +
explicit require_role). Evidence captured for an alert is listed per item.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.alerts import ALERT_SOURCES, AlertAck, EvidenceItem
from app.models.camera import Camera
from app.models.humanless import LossPreventionAlert, StoreSession
from app.models.user import User, UserRole
from app.models.zones import FireIncident, Zone, ZoneEvent
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user, require_role
from app.services.crud import AuditService

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])

Source = Literal["zone_event", "fire_incident", "humanless"]

_ZONE_SEVERITY = {"overcrowding": "high", "long_queue": "medium", "dwell_overrun": "medium", "intrusion": "high"}
_KIND_LABEL = {"overcrowding": "Overcrowding", "long_queue": "Long queue", "dwell_overrun": "Dwell time exceeded",
               "intrusion": "Restricted area entered"}


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _utc(dt).isoformat() if dt is not None else None


# ─────────────────────────── the union ───────────────────────────


def _ack_join(source: str, id_col):
    return and_(AlertAck.source == source, AlertAck.source_id == id_col)


def _union():
    """(source, id, camera_id, zone_id, kind, ts, acked) over every alert source."""
    zone_kind = func.coalesce(ZoneEvent.payload["alert_type"].as_string(), "alert")
    zone_q = (
        select(
            literal("zone_event").label("source"), ZoneEvent.id.label("id"),
            ZoneEvent.camera_id.label("camera_id"), ZoneEvent.zone_id.label("zone_id"),
            zone_kind.label("kind"), ZoneEvent.ts.label("ts"), AlertAck.id.is_not(None).label("acked"),
        )
        .outerjoin(AlertAck, _ack_join("zone_event", ZoneEvent.id))
        .where(
            ZoneEvent.event_type == "alert",
            func.coalesce(ZoneEvent.payload["action"].as_string(), "open") == "open",
        )
    )
    fire_q = (
        select(
            literal("fire_incident").label("source"), FireIncident.id.label("id"),
            FireIncident.camera_id.label("camera_id"), literal(None).label("zone_id"),
            literal("fire").label("kind"), FireIncident.started_at.label("ts"),
            AlertAck.id.is_not(None).label("acked"),
        )
        .outerjoin(AlertAck, _ack_join("fire_incident", FireIncident.id))
    )
    hl_q = (
        select(
            literal("humanless").label("source"), LossPreventionAlert.id.label("id"),
            StoreSession.last_camera_id.label("camera_id"), literal(None).label("zone_id"),
            LossPreventionAlert.alert_type.label("kind"), LossPreventionAlert.timestamp.label("ts"),
            AlertAck.id.is_not(None).label("acked"),
        )
        .outerjoin(StoreSession, StoreSession.id == LossPreventionAlert.session_id)
        .outerjoin(AlertAck, _ack_join("humanless", LossPreventionAlert.id))
    )
    return union_all(zone_q, fire_q, hl_q).subquery("alerts")


def _filters(u, source, camera_id, zone_id, kind, acked, since, until) -> list:
    conds = []
    if source:
        conds.append(u.c.source == source)
    if camera_id is not None:
        conds.append(u.c.camera_id == camera_id)
    if zone_id is not None:
        conds.append(u.c.zone_id == zone_id)
    if kind:
        conds.append(u.c.kind == kind)
    if acked is not None:
        conds.append(u.c.acked.is_(acked))
    if since is not None:
        conds.append(u.c.ts >= _utc(since))
    if until is not None:
        conds.append(u.c.ts < _utc(until))
    return conds


# ─────────────────────────── hydration ───────────────────────────


async def _names(db: AsyncSession, model, ids: Iterable[Optional[int]]) -> Dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    rows = (await db.execute(select(model.id, model.name).where(model.id.in_(ids)))).all()
    return {r[0]: r[1] for r in rows}


async def _acks(db: AsyncSession, keys: List[Tuple[str, int]]) -> Dict[Tuple[str, int], dict]:
    if not keys:
        return {}
    rows = (
        await db.execute(
            select(AlertAck, User.username)
            .outerjoin(User, User.id == AlertAck.acknowledged_by)
            .where(or_(*[and_(AlertAck.source == s, AlertAck.source_id == i) for s, i in keys]))
        )
    ).all()
    return {
        (a.source, a.source_id): {"acked_by": name, "acked_by_id": a.acknowledged_by, "acked_at": _iso(a.acknowledged_at),
                                  "ack_note": a.note}
        for a, name in rows
    }


async def _evidence(db: AsyncSession, keys: List[Tuple[str, int]], uids: List[Tuple[str, str]]) -> Dict[Tuple[str, Any], list]:
    conds = [and_(EvidenceItem.source == s, EvidenceItem.source_id == i) for s, i in keys]
    conds += [and_(EvidenceItem.source == s, EvidenceItem.event_uid == u) for s, u in uids]
    if not conds:
        return {}
    rows = (
        await db.execute(select(EvidenceItem).where(or_(*conds)).order_by(EvidenceItem.created_at, EvidenceItem.id))
    ).scalars().all()
    out: Dict[Tuple[str, Any], list] = {}
    for r in rows:
        entry = {"id": r.id, "kind": r.kind}
        if r.source_id is not None:
            out.setdefault((r.source, r.source_id), []).append(entry)
        if r.event_uid:
            out.setdefault((r.source, "uid:" + r.event_uid), []).append(entry)
    return out


async def _zone_close_times(db: AsyncSession, opens: List[ZoneEvent]) -> Dict[int, datetime]:
    """ended_at for zone alerts: the close event naming the open's event_uid; rows written
    before event_uid existed fall back to the next close of the same zone/kind(/track)."""
    if not opens:
        return {}
    zone_ids = {e.zone_id for e in opens if e.zone_id is not None}
    if not zone_ids:
        return {}
    since = min(_utc(e.ts) for e in opens)
    closes = (
        await db.execute(
            select(ZoneEvent)
            .where(
                ZoneEvent.zone_id.in_(zone_ids), ZoneEvent.event_type == "alert", ZoneEvent.ts >= since,
                ZoneEvent.payload["action"].as_string() == "close",
            )
            .order_by(ZoneEvent.ts)
            .limit(20000)
        )
    ).scalars().all()
    by_uid = {}
    for c in closes:
        uid = (c.payload or {}).get("open_event_uid")
        if uid and uid not in by_uid:
            by_uid[uid] = _utc(c.ts)
    out: Dict[int, datetime] = {}
    for e in opens:
        if e.event_uid:
            if e.event_uid in by_uid:
                out[e.id] = by_uid[e.event_uid]
            continue
        kind = (e.payload or {}).get("alert_type")
        for c in closes:
            cp = c.payload or {}
            if (c.zone_id == e.zone_id and _utc(c.ts) >= _utc(e.ts) and cp.get("alert_type") == kind
                    and not cp.get("open_event_uid") and (kind not in ("dwell_overrun", "intrusion") or c.track_id == e.track_id)):
                out[e.id] = _utc(c.ts)
                break
    return out


def _zone_message(e: ZoneEvent, zone_name: Optional[str]) -> str:
    p = e.payload or {}
    kind = p.get("alert_type") or "alert"
    where = zone_name or p.get("zone_name") or (f"zone {e.zone_id}" if e.zone_id else "camera")
    label = _KIND_LABEL.get(kind, kind.replace("_", " ").capitalize())
    if kind == "overcrowding" and p.get("capacity"):
        return f"{label} in {where}: {p.get('count', 0):.0f} people, capacity {p['capacity']}"
    if kind == "long_queue" and p.get("queue_length") is not None:
        return f"{label} in {where}: {float(p['queue_length']):.0f} waiting (threshold {p.get('threshold')})"
    if kind == "dwell_overrun":
        who = f"track {e.track_id}" if e.track_id is not None else "a person"
        return f"{label} in {where}: {who} stayed over {float(p.get('threshold_s') or 0):.0f} s"
    if kind == "intrusion":
        who = f"track {e.track_id}" if e.track_id is not None else "a person"
        return f"{label}: {who} in {where}"
    return f"{label} in {where}"


async def _hydrate(db: AsyncSession, page: List[Any]) -> List[dict]:
    ids: Dict[str, List[int]] = {s: [] for s in ALERT_SOURCES}
    for r in page:
        ids[r.source].append(r.id)
    zone_rows = {
        e.id: e for e in (await db.execute(select(ZoneEvent).where(ZoneEvent.id.in_(ids["zone_event"])))).scalars()
    } if ids["zone_event"] else {}
    fire_rows = {
        f.id: f for f in (await db.execute(select(FireIncident).where(FireIncident.id.in_(ids["fire_incident"])))).scalars()
    } if ids["fire_incident"] else {}
    hl_rows = {
        a.id: a for a in (
            await db.execute(select(LossPreventionAlert).where(LossPreventionAlert.id.in_(ids["humanless"])))
        ).scalars()
    } if ids["humanless"] else {}

    keys = [(r.source, r.id) for r in page]
    uids = [("zone_event", e.event_uid) for e in zone_rows.values() if e.event_uid]
    uids += [("fire_incident", f.incident_key[:36]) for f in fire_rows.values() if f.incident_key]
    cams = await _names(db, Camera, (r.camera_id for r in page))
    zones = await _names(db, Zone, (r.zone_id for r in page))
    acks = await _acks(db, keys)
    evidence = await _evidence(db, keys, uids)
    ended = await _zone_close_times(db, list(zone_rows.values()))

    items = []
    for r in page:
        item = {
            "source": r.source, "id": r.id, "camera_id": r.camera_id, "camera_name": cams.get(r.camera_id),
            "zone_id": r.zone_id, "zone_name": zones.get(r.zone_id), "kind": r.kind, "severity": "medium",
            "ts": _iso(r.ts), "ended_at": None, "message": "", "value": None, "track_id": None, "uid": None,
        }
        if r.source == "zone_event" and r.id in zone_rows:
            e = zone_rows[r.id]
            p = e.payload or {}
            item["zone_name"] = item["zone_name"] or p.get("zone_name")
            item.update(severity=_ZONE_SEVERITY.get(r.kind, "medium"), ended_at=_iso(ended.get(e.id)),
                        message=_zone_message(e, item["zone_name"]), value=e.value, track_id=e.track_id, uid=e.event_uid)
        elif r.source == "fire_incident" and r.id in fire_rows:
            f = fire_rows[r.id]
            item.update(severity="critical", ended_at=_iso(f.cleared_at), value=f.peak_conf, uid=f.incident_key,
                        message=f"{(f.class_name or 'fire').capitalize()} detected (peak confidence {float(f.peak_conf or 0):.0%})"
                        + (", cleared" if f.status == "cleared" else ", active"))
        elif r.source == "humanless" and r.id in hl_rows:
            a = hl_rows[r.id]
            item.update(severity=a.severity or "medium", value=a.confidence,
                        message=a.description or r.kind.replace("_", " ").capitalize())
        ack = acks.get((r.source, r.id))
        item.update(acked=ack is not None, acked_by=(ack or {}).get("acked_by"), acked_by_id=(ack or {}).get("acked_by_id"),
                    acked_at=(ack or {}).get("acked_at"), ack_note=(ack or {}).get("ack_note"))
        ev = list(evidence.get((r.source, r.id), []))
        if item["uid"]:
            for x in evidence.get((r.source, "uid:" + str(item["uid"])[:36]), []):
                if x not in ev:
                    ev.append(x)
        item["evidence"] = ev
        items.append(item)
    return items


# ─────────────────────────── endpoints ───────────────────────────


@router.get("")
async def list_alerts(
    source: Optional[Source] = None,
    camera_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    kind: Optional[str] = Query(None, max_length=60),
    acked: Optional[bool] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    u = _union()
    conds = _filters(u, source, camera_id, zone_id, kind, acked, since, until)
    total = int((await db.execute(select(func.count()).select_from(u).where(*conds))).scalar_one())
    page = (
        await db.execute(
            select(u).where(*conds).order_by(u.c.ts.desc(), u.c.source, u.c.id.desc()).limit(limit).offset(offset)
        )
    ).all()
    items = await _hydrate(db, page)
    out: Dict[str, Any] = {"items": items, "total": total, "limit": limit, "offset": offset}
    if total == 0:
        out["status"] = "no_data"
    return out


@router.get("/facets")
async def alert_facets(
    source: Optional[Source] = None,
    camera_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    kind: Optional[str] = Query(None, max_length=60),
    acked: Optional[bool] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    u = _union()
    conds = _filters(u, source, camera_id, zone_id, kind, acked, since, until)
    sources = {s: 0 for s in ALERT_SOURCES}
    for s, n in (await db.execute(select(u.c.source, func.count()).where(*conds).group_by(u.c.source))).all():
        sources[s] = int(n)
    kinds = {
        k: int(n)
        for k, n in (await db.execute(select(u.c.kind, func.count()).where(*conds).group_by(u.c.kind).order_by(func.count().desc()))).all()
    }
    cam_rows = (
        await db.execute(
            select(u.c.camera_id, Camera.name, func.count())
            .select_from(u.outerjoin(Camera, Camera.id == u.c.camera_id))
            .where(*conds)
            .group_by(u.c.camera_id, Camera.name)
            .order_by(func.count().desc())
        )
    ).all()
    unacked = int(
        (await db.execute(select(func.count()).select_from(u).where(*conds, u.c.acked.is_(False)))).scalar_one()
    )
    out: Dict[str, Any] = {
        "sources": sources,
        "kinds": kinds,
        "cameras": [{"id": c, "name": name, "n": int(n)} for c, name, n in cam_rows],
        "unacked": unacked,
    }
    if not any(sources.values()):
        out["status"] = "no_data"
    return out


class AckIn(BaseModel):
    note: Optional[str] = Field(None, max_length=1000)


_SOURCE_MODEL = {"zone_event": ZoneEvent, "fire_incident": FireIncident, "humanless": LossPreventionAlert}


@router.post("/{source}/{alert_id}/ack")
async def acknowledge_alert(
    source: Source,
    alert_id: int,
    request: Request,
    body: Optional[AckIn] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Acknowledge an alert (idempotent: a second ack keeps the first acknowledger)."""
    model = _SOURCE_MODEL[source]
    row = await db.get(model, alert_id)
    if row is None or (source == "zone_event" and (row.event_type != "alert" or (row.payload or {}).get("action", "open") != "open")):
        raise HTTPException(status_code=404, detail="Alert not found")
    note = (body.note.strip() or None) if body and body.note else None
    res = await db.execute(
        pg_insert(AlertAck)
        .values(source=source, source_id=alert_id, acknowledged_by=user.id, note=note)
        .on_conflict_do_nothing(constraint="uq_alert_acks_source")
        .returning(AlertAck.id)
    )
    created = res.scalar_one_or_none() is not None
    if created:
        await AuditService.log_event(
            db, "alert_acknowledged", user.id, f"Acknowledged {source} alert #{alert_id}",
            metadata={"source": source, "source_id": alert_id, "note": note},
            ip_address=client_ip(request), user_agent=request.headers.get("user-agent"),
        )
    await db.commit()

    u = _union()
    page = (await db.execute(select(u).where(u.c.source == source, u.c.id == alert_id))).all()
    items = await _hydrate(db, page)
    if not items:
        raise HTTPException(status_code=404, detail="Alert not found")
    return items[0]

