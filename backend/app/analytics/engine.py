"""
ZoneAnalyticsEngine: turns confirmed tracks, fire detections and classified
faces into zone analytics events (Remediation Plan, Phase 3).

Contract (used by the processing pipeline):

    engine = get_engine()
    engine.set_zones(camera_id, zones)                    # on start + on "zones_changed"
    events = engine.on_tracks(camera_id, ts, (w, h), tracks)   # CONFIRMED tracks only
    events += engine.on_track_lost(camera_id, ts, track_id)
    events += engine.on_fire(camera_id, ts, detections)  # every fire inference, incl. empty
    events += engine.on_faces(camera_id, ts, faces)
    await zones_repo.persist_events(events)               # off the frame loop

`ts` is wall-clock UNIX time in seconds (time.time(), or a recording's start
time + its frame offset). Persisted events are stored at that time.

Threading: every public method takes one re-entrant lock, so the engine may be
called from several camera threads. Each call is pure Python over the tracks
of one frame (no I/O, no database); the cost is O(tracks x zones).

All per-track state is keyed by (camera_id, track_id) and is dropped in
on_track_lost (and for tracks unseen for TRACK_STALE_S as a safety net).

Event dicts all carry: type, camera_id, ts, zone_id (or None) and
track_id / global_id / duration_s / value / payload where meaningful.
Event types:
    zone_visit, zone_pass          presence ended in a shelf or crowd zone
    queue_wait, queue_abandon      queue -> counter wait, or left the queue
    service                        presence ended at a counter (<= 30 min)
    line_cross                     entry line crossed (value +1 in / -1 out)
    crowd_level                    smoothed crowd level changed
    zone_sample                    5 s sample of a zone's smoothed count
                                   (zone_id None = whole-camera occupancy)
    alert                          overcrowding / long_queue / dwell_overrun open or
                                   close; every alert carries "event_uid" (uuid4 str)
                                   and a close carries payload.open_event_uid
    fire                           fire incident start / reopen / clear
    track_visit                    a confirmed track ended (first/last seen)
    emotion_bucket                 cumulative 5-min emotion counts per zone
    vibe_sample                    store vibe with components (60 s)
    heatmap_bucket                 1-min spatial grid of person-seconds per cell
                                   (foot points of confirmed tracks)
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.analytics import definitions as d

logger = logging.getLogger(__name__)

ZONE_TYPES = ("crowd", "shelf", "queue", "counter", "entry_line", "restricted")

SAMPLE_INTERVAL_S = 5.0          # zone_sample cadence per camera
OVERCROWD_HOLD_S = 60.0          # critical (>= 90 % capacity) this long => alert
LONG_QUEUE_LENGTH = 5            # queue length that counts as long
LONG_QUEUE_HOLD_S = 120.0        # long this long => alert
ALERT_CLEAR_HOLD_S = 30.0        # condition gone this long => alert closes
TRACK_STALE_S = 60.0             # unseen this long without on_track_lost => finalised
TRACK_VISIT_MIN_S = 1.0          # shorter confirmed tracks are fragments, not visits
INTRUSION_CONFIRM_S = 2.0       # inside a restricted zone this long => intrusion alert (filters edge jitter)
DWELL_SAME_PERSON_GAP_S = 60.0   # a global ID unseen in a zone this long may raise a new dwell alert
EMOTION_EMIT_S = 30.0            # cumulative emotion bucket upsert cadence
VIBE_INTERVAL_S = 60.0           # store vibe sample cadence
VIBE_WINDOW_S = 900.0            # engagement / sentiment window (15 min)
FLOW_WINDOW_S = 300.0            # arrivals window (5 min)
CAMERA_STALE_S = 30.0            # camera without frames this long is left out of vibe
VIBE_WARMUP_S = 10.0             # occupancy from a camera counts once tracks had time to confirm
TS_RESET_S = 5.0                 # ts going back more than this => new recording
HEATMAP_GRID = (48, 27)          # columns x rows over the normalised frame (16:9)
HEATMAP_BUCKET_S = 60            # one persisted grid per camera per minute
HEATMAP_MAX_DT_S = 1.0           # longer frame gaps count as this (a stalled feed adds no heat)


@dataclass(frozen=True)
class TrackObs:
    track_id: int
    global_id: str | None
    xyxy: tuple[float, float, float, float]
    conf: float


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _Zone:
    id: Any
    name: str
    zone_type: str
    points: Tuple[Tuple[float, float], ...]
    capacity: Optional[int]
    lane: str
    in_sign: int
    dwell_alert_s: Optional[float] = None   # config.dwell_alert_seconds (None = off)

    def geometry_key(self) -> tuple:
        return (self.zone_type, self.points, self.capacity, self.lane, self.in_sign)


@dataclass
class _ZoneStats:
    """Running in-memory stats since the zone was (re)loaded, for live views."""

    passers: int = 0
    visits: int = 0
    engaged: int = 0
    dwell: Deque[float] = field(default_factory=lambda: deque(maxlen=500))
    crossings_in: int = 0
    crossings_out: int = 0


@dataclass
class _ZoneLive:
    median: d.RollingMedian
    raw_count: int = 0
    level_idx: Optional[int] = None
    hot_since: Optional[float] = None     # condition for an alert holding since
    cool_since: Optional[float] = None    # condition gone since (alert open)
    alert_open: bool = False
    alert_uid: Optional[str] = None       # event_uid of the open alert
    stats: _ZoneStats = field(default_factory=_ZoneStats)


@dataclass
class _QueueHist:
    first_in: float
    last_in: float
    qualified: bool = False   # queue presence lasted >= QUEUE_MIN_PRESENCE_S
    ended: bool = False       # queue presence ended (left the queue)


@dataclass
class _LaneStats:
    waits: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=500))
    services: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=500))
    abandons: Deque[float] = field(default_factory=lambda: deque(maxlen=500))


@dataclass
class _Arrival:
    first_ts: float
    last_ts: float
    gid: Optional[str] = None
    crossed: bool = False      # entry-line "in" (a deliberate crossing, never a flicker)


@dataclass
class _Camera:
    key: str
    zones: List[_Zone] = field(default_factory=list)
    frame_wh: Optional[Tuple[int, int]] = None
    last_ts: Optional[float] = None
    # per track
    first_seen: Dict[int, float] = field(default_factory=dict)
    last_seen: Dict[int, float] = field(default_factory=dict)
    global_ids: Dict[int, str] = field(default_factory=dict)
    last_box: Dict[int, Tuple[float, float, float, float]] = field(default_factory=dict)
    presences: Dict[Tuple[Any, int], d.PresenceState] = field(default_factory=dict)
    lines: Dict[Tuple[Any, int], d.LineCrossingState] = field(default_factory=dict)
    queue_hist: Dict[Tuple[str, int], _QueueHist] = field(default_factory=dict)
    dwell_alerts: Dict[Tuple[Any, int], str] = field(default_factory=dict)  # (zone, track) -> open uid
    dwell_gids: Dict[Tuple[Any, str], float] = field(default_factory=dict)  # (zone, global id) -> last seen inside
    # per zone
    live: Dict[Any, _ZoneLive] = field(default_factory=dict)
    lanes: Dict[str, _LaneStats] = field(default_factory=dict)
    occupancy: d.RollingMedian = field(default_factory=lambda: d.RollingMedian(d.CROWD_SMOOTHING_S))
    last_sample_ts: Optional[float] = None
    # vibe inputs
    arrivals: Deque[Tuple[float, int]] = field(default_factory=deque)  # (ts, track): entry-line "in" or new tracks
    arrival_meta: Dict[int, _Arrival] = field(default_factory=dict)      # outlives the track, for dedupe by identity
    visits: Deque[Tuple[float, bool]] = field(default_factory=deque)  # (ts, engaged) shelf visits
    faces: Deque[Tuple[float, str]] = field(default_factory=deque)    # (ts, label)
    started_ts: Optional[float] = None
    # fire
    fire: d.FireState = field(default_factory=d.FireState)
    fire_key: Optional[str] = None
    # emotion buckets: (zone_id or None, bucket_start) -> {label: n}
    emotion: Dict[Tuple[Any, float], Dict[str, int]] = field(default_factory=dict)
    emotion_dirty: Set[Tuple[Any, float]] = field(default_factory=set)
    last_emotion_emit: Optional[float] = None
    # spatial heatmap: open bucket of person-seconds per grid cell
    heat_bucket: Optional[float] = None
    heat_cells: Dict[int, float] = field(default_factory=dict)
    heat_tracks: Set[int] = field(default_factory=set)
    heat_last_ts: Optional[float] = None


def _coerce_zone(raw: dict) -> Optional[_Zone]:
    zone_type = str(raw.get("zone_type") or "").lower()
    if zone_type not in ZONE_TYPES:
        logger.warning("Ignoring zone %s with unknown type %r", raw.get("id"), zone_type)
        return None
    if raw.get("is_active") is False:
        return None
    try:
        pts = d.validate_polygon(raw.get("points") or [], zone_type)
    except ValueError as exc:
        logger.warning("Ignoring zone %s: %s", raw.get("id"), exc)
        return None
    cfg = raw.get("config") or {}
    in_sign = -1 if int(cfg.get("in_sign", 1) or 1) < 0 else 1
    cap = raw.get("capacity")
    cap = int(cap) if cap not in (None, "") and int(cap) > 0 else None
    lane = raw.get("lane")
    return _Zone(
        id=raw.get("id"),
        name=str(raw.get("name") or f"zone-{raw.get('id')}"),
        zone_type=zone_type,
        points=tuple((float(x), float(y)) for x, y in pts),
        capacity=cap,
        lane=str(lane).strip() if lane not in (None, "") else "default",
        in_sign=in_sign,
        dwell_alert_s=(INTRUSION_CONFIRM_S if zone_type == "restricted"
                       else None if zone_type == "entry_line" else d.dwell_alert_threshold(cfg)),
    )


def _alert_type(z: _Zone) -> str:
    """Per-person zone alert raised by `_dwell_overrun` for this zone."""
    return "intrusion" if z.zone_type == "restricted" else "dwell_overrun"


def _ev(cam: _Camera, etype: str, ts: float, **kw) -> dict:
    ev = {"type": etype, "camera_id": cam.key, "ts": float(ts), "zone_id": None}
    ev.update(kw)
    if etype == "alert" and not ev.get("event_uid"):
        ev["event_uid"] = str(uuid.uuid4())
    return ev


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ZoneAnalyticsEngine:
    """Thread-safe zone analytics state machine (see module docstring)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cams: Dict[str, _Camera] = {}
        self._last_vibe_ts: Optional[float] = None
        self._latest_vibe: Optional[dict] = None

    # ------------------------------------------------------------------ zones

    def _cam(self, camera_id) -> _Camera:
        key = str(camera_id)
        cam = self._cams.get(key)
        if cam is None:
            cam = _Camera(key=key)
            self._cams[key] = cam
        return cam

    def set_zones(self, camera_id: str, zones: list[dict]) -> None:
        """Replace a camera's zones. Unchanged zones keep their live state;
        state of removed or re-drawn zones is discarded (no events)."""
        parsed = [z for z in (_coerce_zone(z) for z in zones or []) if z is not None]
        with self._lock:
            cam = self._cam(camera_id)
            old = {z.id: z for z in cam.zones}
            keep: Set[Any] = set()
            for z in parsed:
                prev = old.get(z.id)
                if prev is not None and prev.geometry_key() == z.geometry_key():
                    keep.add(z.id)
            cam.zones = parsed
            valid_ids = {z.id for z in parsed}
            for zid in list(cam.live):
                if zid not in keep:
                    del cam.live[zid]
            for z in parsed:
                if z.id not in cam.live:
                    window = d.QUEUE_SMOOTHING_S if z.zone_type == "queue" else d.CROWD_SMOOTHING_S
                    cam.live[z.id] = _ZoneLive(median=d.RollingMedian(window))
            for k in [k for k in cam.presences if k[0] not in keep or k[0] not in valid_ids]:
                del cam.presences[k]
            for k in [k for k in cam.lines if k[0] not in keep or k[0] not in valid_ids]:
                del cam.lines[k]
            dwell_on = {z.id for z in parsed if z.dwell_alert_s}
            for k in [k for k in cam.dwell_alerts if k[0] not in keep or k[0] not in dwell_on]:
                del cam.dwell_alerts[k]
            for k in [k for k in cam.dwell_gids if k[0] not in keep or k[0] not in dwell_on]:
                del cam.dwell_gids[k]
            lanes = {z.lane for z in parsed if z.zone_type in ("queue", "counter")}
            for k in [k for k in cam.queue_hist if k[0] not in lanes]:
                del cam.queue_hist[k]
            for lane in list(cam.lanes):
                if lane not in lanes:
                    del cam.lanes[lane]

    def get_zones(self, camera_id) -> List[dict]:
        with self._lock:
            cam = self._cams.get(str(camera_id))
            return [] if cam is None else [self._zone_dict(z) for z in cam.zones]

    @staticmethod
    def _zone_dict(z: _Zone) -> dict:
        return {
            "id": z.id, "name": z.name, "zone_type": z.zone_type,
            "points": [list(p) for p in z.points], "capacity": z.capacity,
            "lane": z.lane, "in_sign": z.in_sign,
        }

    # ------------------------------------------------------------------ tracks

    def on_tracks(
        self,
        camera_id: str,
        ts: float,
        frame_wh: tuple[int, int],
        tracks: list[TrackObs],
    ) -> list[dict]:
        events: List[dict] = []
        ts = float(ts)
        with self._lock:
            cam = self._cam(camera_id)
            if cam.last_ts is not None and ts < cam.last_ts - TS_RESET_S:
                # Time went backwards (recording restarted): close everything first.
                events += self._flush(cam, cam.last_ts)
            if cam.started_ts is None:
                cam.started_ts = ts
            cam.last_ts = ts
            if frame_wh and frame_wh[0] and frame_wh[1]:
                cam.frame_wh = (int(frame_wh[0]), int(frame_wh[1]))
            has_line = any(z.zone_type == "entry_line" for z in cam.zones)

            # Track bookkeeping + foot points
            feet: Dict[int, Tuple[float, float]] = {}
            for t in tracks:
                tid = int(t.track_id)
                if tid not in cam.first_seen:
                    cam.first_seen[tid] = ts
                    if not has_line:
                        cam.arrivals.append((ts, tid))
                        cam.arrival_meta.setdefault(tid, _Arrival(first_ts=ts, last_ts=ts))
                cam.last_seen[tid] = ts
                if t.global_id:
                    cam.global_ids[tid] = str(t.global_id)
                am = cam.arrival_meta.get(tid)
                if am is not None:
                    am.last_ts = ts
                    if t.global_id:
                        am.gid = str(t.global_id)
                cam.last_box[tid] = tuple(float(v) for v in t.xyxy[:4])
                if cam.frame_wh:
                    feet[tid] = d.normalized_foot_point(t.xyxy, cam.frame_wh)

            cam.occupancy.add(ts, len(feet) if cam.frame_wh else len(tracks))
            events += self._heat_step(cam, ts, feet)

            for z in cam.zones:
                if z.zone_type == "entry_line":
                    events += self._step_line(cam, z, ts, feet)
                else:
                    events += self._step_zone(cam, z, ts, feet)

            events += self._expire_queue(cam, ts)
            events += self._expire_stale_tracks(cam, ts)
            fire_ev = self._fire_clear(cam, ts)
            if fire_ev:
                events.append(fire_ev)

            if cam.last_sample_ts is None or ts - cam.last_sample_ts >= SAMPLE_INTERVAL_S:
                cam.last_sample_ts = ts
                events += self._samples(cam, ts)

            self._trim_windows(cam, ts)
            events += self._emotion_emit(cam, ts, force=False)
            vibe = self._maybe_vibe(ts)
            if vibe:
                events.append(vibe)
        return events

    # ---------------------------------------------------------------- heatmap

    def _heat_step(self, cam: _Camera, ts: float, feet: Dict[int, Tuple[float, float]]) -> List[dict]:
        """Add each confirmed track's foot point, weighted by the frame interval (person-seconds)."""
        events: List[dict] = []
        bucket = d.bucket_start(ts, HEATMAP_BUCKET_S)
        if cam.heat_bucket != bucket:
            ev = self._heat_emit(cam)
            if ev:
                events.append(ev)
            cam.heat_bucket, cam.heat_cells, cam.heat_tracks = bucket, {}, set()
        dt = 0.0 if cam.heat_last_ts is None else min(max(ts - cam.heat_last_ts, 0.0), HEATMAP_MAX_DT_S)
        cam.heat_last_ts = ts
        if dt <= 0.0:
            return events
        gw, gh = HEATMAP_GRID
        for tid, (x, y) in feet.items():
            cx = min(gw - 1, max(0, int(x * gw)))
            cy = min(gh - 1, max(0, int(y * gh)))
            idx = cy * gw + cx
            cam.heat_cells[idx] = cam.heat_cells.get(idx, 0.0) + dt
            cam.heat_tracks.add(tid)
        return events

    def _heat_payload(self, cam: _Camera) -> Optional[dict]:
        if cam.heat_bucket is None or not cam.heat_cells:
            return None
        return {
            "grid": list(HEATMAP_GRID),
            "cells": [[i, round(v, 3)] for i, v in sorted(cam.heat_cells.items())],
            "tracks": len(cam.heat_tracks),
            "bucket_s": HEATMAP_BUCKET_S,
            "end_ts": cam.heat_last_ts,
            "frame_wh": list(cam.frame_wh) if cam.frame_wh else None,
        }

    def _heat_emit(self, cam: _Camera) -> Optional[dict]:
        payload = self._heat_payload(cam)
        if payload is None:
            return None
        total = round(sum(v for _, v in payload["cells"]), 3)
        return _ev(cam, "heatmap_bucket", cam.heat_bucket, value=total, payload=payload)

    def heatmap_open_bucket(self, camera_id) -> Optional[dict]:
        """The current, not yet persisted bucket ({start_ts, **payload}) or None."""
        with self._lock:
            cam = self._cams.get(str(camera_id))
            if cam is None:
                return None
            payload = self._heat_payload(cam)
            return {"start_ts": cam.heat_bucket, **payload} if payload else None

    def _step_zone(self, cam: _Camera, z: _Zone, ts: float, feet: Dict[int, Tuple[float, float]]) -> List[dict]:
        events: List[dict] = []
        live = cam.live[z.id]
        inside_ids = {tid for tid, p in feet.items() if d.point_in_polygon(p, z.points)}
        live.raw_count = len(inside_ids)
        smoothed = live.median.add(ts, len(inside_ids))

        # Presences: every currently visible track is stepped (inside or not).
        for tid in feet:
            key = (z.id, tid)
            state = cam.presences.get(key)
            new_state, upd = d.presence_step(state, ts, tid in inside_ids)
            if new_state is None:
                cam.presences.pop(key, None)
            else:
                cam.presences[key] = new_state
            if upd.entered:
                events += self._on_enter(cam, z, ts, tid)
            if upd.ended is not None:
                events += self._on_presence_end(cam, z, ts, tid, upd.ended)
            elif new_state is not None and z.zone_type == "queue":
                qh = cam.queue_hist.get((z.lane, tid))
                if qh is not None and tid in inside_ids:
                    qh.last_in = ts
                    qh.ended = False
                    if not qh.qualified and ts - qh.first_in >= d.QUEUE_MIN_PRESENCE_S:
                        qh.qualified = True

        if z.dwell_alert_s:
            events += self._dwell_overrun(cam, z, ts, inside_ids)
        if z.zone_type == "crowd":
            events += self._crowd_level(cam, z, live, ts, smoothed)
        elif z.zone_type == "queue":
            events += self._queue_alert(cam, z, live, ts, smoothed)
        return events

    def _dwell_overrun(self, cam: _Camera, z: _Zone, ts: float, inside_ids: Set[int]) -> List[dict]:
        """One alert per visit that lasts longer than the zone's threshold.

        Restricted zones use the same path with a short fixed threshold
        (INTRUSION_CONFIRM_S) and alert type "intrusion".

        A visit is a (zone, track) presence. When the track has a global Re-ID
        identity, the alert is also deduplicated per (zone, global id): a person
        whose track breaks and resumes under a new ID while still in the zone is
        not alerted twice. The identity may alert again only after being out of
        the zone for DWELL_SAME_PERSON_GAP_S.
        """
        events: List[dict] = []
        alert_type = _alert_type(z)
        for k in [k for k, seen in cam.dwell_gids.items() if k[0] == z.id and ts - seen > DWELL_SAME_PERSON_GAP_S]:
            del cam.dwell_gids[k]
        for tid in inside_ids:
            gid = cam.global_ids.get(tid)
            gkey = (z.id, str(gid)) if gid is not None else None
            if gkey is not None and gkey in cam.dwell_gids:
                cam.dwell_gids[gkey] = ts
            key = (z.id, tid)
            st = cam.presences.get(key)
            if st is None or key in cam.dwell_alerts:
                continue
            dwell = ts - st.first_in
            if dwell < z.dwell_alert_s or gkey in cam.dwell_gids:
                continue
            box = cam.last_box.get(tid)
            ev = _ev(cam, "alert", ts, zone_id=z.id, track_id=tid, global_id=gid,
                     duration_s=round(dwell, 2), value=round(dwell, 2),
                     bbox_xyxy=list(box) if box else None,
                     frame_wh=list(cam.frame_wh) if cam.frame_wh else None,
                     payload={"alert_type": alert_type, "action": "open", "zone_name": z.name,
                              "since_ts": st.first_in, "threshold_s": z.dwell_alert_s,
                              "zone_type": z.zone_type})
            cam.dwell_alerts[key] = ev["event_uid"]
            if gkey is not None:
                cam.dwell_gids[gkey] = ts
            events.append(ev)
        return events

    def _dwell_close(self, cam: _Camera, z: _Zone, ts: float, tid: int, st: d.PresenceState) -> List[dict]:
        uid = cam.dwell_alerts.pop((z.id, tid), None)
        if uid is None:
            return []
        dwell = round(d.dwell_seconds(st), 2)
        return [_ev(cam, "alert", st.last_in, zone_id=z.id, track_id=tid, global_id=cam.global_ids.get(tid),
                    duration_s=dwell, value=dwell,
                    payload={"alert_type": _alert_type(z), "action": "close", "zone_name": z.name,
                             "open_event_uid": uid, "since_ts": st.first_in, "threshold_s": z.dwell_alert_s,
                             "zone_type": z.zone_type})]

    def _on_enter(self, cam: _Camera, z: _Zone, ts: float, tid: int) -> List[dict]:
        events: List[dict] = []
        if z.zone_type == "queue":
            key = (z.lane, tid)
            if key not in cam.queue_hist:
                cam.queue_hist[key] = _QueueHist(first_in=ts, last_in=ts)
            else:
                cam.queue_hist[key].ended = False
                cam.queue_hist[key].last_in = ts
        elif z.zone_type == "counter":
            qh = cam.queue_hist.pop((z.lane, tid), None)
            if qh is not None and ts - qh.last_in <= d.QUEUE_TO_COUNTER_GRACE_S:
                wait = max(0.0, ts - qh.first_in)
                cam.lanes.setdefault(z.lane, _LaneStats()).waits.append((ts, wait))
                events.append(_ev(
                    cam, "queue_wait", ts, zone_id=self._lane_zone(cam, z.lane, "queue"),
                    track_id=tid, global_id=cam.global_ids.get(tid), duration_s=round(wait, 2),
                    payload={"lane": z.lane, "counter_zone_id": z.id, "queue_entered_ts": qh.first_in},
                ))
        return events

    def _on_presence_end(self, cam: _Camera, z: _Zone, ts: float, tid: int, st: d.PresenceState) -> List[dict]:
        return self._dwell_close(cam, z, ts, tid, st) + self._presence_end_events(cam, z, ts, tid, st)

    def _presence_end_events(self, cam: _Camera, z: _Zone, ts: float, tid: int, st: d.PresenceState) -> List[dict]:
        dwell = round(d.dwell_seconds(st), 2)
        gid = cam.global_ids.get(tid)
        stats = cam.live[z.id].stats if z.id in cam.live else None
        if z.zone_type in ("shelf", "crowd", "restricted"):
            engaged = st.visit and dwell > d.ENGAGED_DWELL_S
            if stats is not None:
                stats.passers += 1
                if st.visit:
                    stats.visits += 1
                    stats.dwell.append(dwell)
                    if engaged:
                        stats.engaged += 1
            if st.visit and z.zone_type == "shelf":
                cam.visits.append((st.last_in, engaged))
            return [_ev(
                cam, "zone_visit" if st.visit else "zone_pass", st.last_in, zone_id=z.id,
                track_id=tid, global_id=gid, duration_s=dwell,
                payload={"zone_type": z.zone_type, "engaged": engaged, "entered_ts": st.first_in},
            )]
        if z.zone_type == "queue":
            qh = cam.queue_hist.get((z.lane, tid))
            if qh is not None:
                qh.ended = True
                qh.last_in = st.last_in
                if not qh.qualified and dwell >= d.QUEUE_MIN_PRESENCE_S:
                    qh.qualified = True
            return []
        if z.zone_type == "counter":
            if not st.visit:
                return []
            if dwell > d.SERVICE_MAX_S:
                # Present at the counter for > 30 min: staff, not a customer.
                return []
            cam.lanes.setdefault(z.lane, _LaneStats()).services.append((st.last_in, dwell))
            return [_ev(
                cam, "service", st.last_in, zone_id=z.id, track_id=tid, global_id=gid,
                duration_s=dwell, payload={"lane": z.lane, "entered_ts": st.first_in},
            )]
        return []

    def _lane_zone(self, cam: _Camera, lane: str, zone_type: str):
        for z in cam.zones:
            if z.lane == lane and z.zone_type == zone_type:
                return z.id
        return None

    def _expire_queue(self, cam: _Camera, ts: float) -> List[dict]:
        """Left the queue (>= 3 s in it) and did not reach the counter in time."""
        events: List[dict] = []
        for key, qh in list(cam.queue_hist.items()):
            if qh.ended and ts - qh.last_in > d.QUEUE_TO_COUNTER_GRACE_S:
                del cam.queue_hist[key]
                if qh.qualified:
                    events.append(self._abandon(cam, key, qh))
        return events

    def _abandon(self, cam: _Camera, key: Tuple[str, int], qh: _QueueHist) -> dict:
        lane, tid = key
        dur = round(max(0.0, qh.last_in - qh.first_in), 2)
        cam.lanes.setdefault(lane, _LaneStats()).abandons.append(qh.last_in)
        return _ev(
            cam, "queue_abandon", qh.last_in, zone_id=self._lane_zone(cam, lane, "queue"),
            track_id=tid, global_id=cam.global_ids.get(tid), duration_s=dur,
            payload={"lane": lane},
        )

    def _step_line(self, cam: _Camera, z: _Zone, ts: float, feet: Dict[int, Tuple[float, float]]) -> List[dict]:
        events: List[dict] = []
        a, b = z.points[0], z.points[1]
        stats = cam.live[z.id].stats
        for tid, p in feet.items():
            key = (z.id, tid)
            st = cam.lines.get(key)
            if st is None:
                st = cam.lines[key] = d.LineCrossingState()
            direction = st.update(p, a, b, z.in_sign)
            if direction:
                if direction > 0:
                    stats.crossings_in += 1
                    cam.arrivals.append((ts, tid))
                    am = cam.arrival_meta.setdefault(tid, _Arrival(first_ts=ts, last_ts=ts))
                    am.crossed = True
                    am.gid = am.gid or cam.global_ids.get(tid)
                else:
                    stats.crossings_out += 1
                events.append(_ev(
                    cam, "line_cross", ts, zone_id=z.id, track_id=tid,
                    global_id=cam.global_ids.get(tid), value=float(direction),
                    payload={"direction": "in" if direction > 0 else "out"},
                ))
        cam.live[z.id].raw_count = 0
        return events

    def _crowd_level(self, cam: _Camera, z: _Zone, live: _ZoneLive, ts: float, smoothed: float) -> List[dict]:
        events: List[dict] = []
        if not z.capacity:
            return events
        prev = live.level_idx
        idx = d.crowd_level_index(smoothed, z.capacity, prev)
        pct = round(smoothed / z.capacity, 4)
        if idx != prev:
            live.level_idx = idx
            if prev is not None:
                events.append(_ev(
                    cam, "crowd_level", ts, zone_id=z.id, value=pct,
                    payload={"level": d.crowd_level_name(idx), "prev_level": d.crowd_level_name(prev),
                             "count": smoothed, "capacity": z.capacity},
                ))
        critical = idx is not None and idx >= len(d.CROWD_LEVEL_THRESHOLDS)
        events += self._hold_alert(
            cam, z, live, ts, critical, OVERCROWD_HOLD_S, "overcrowding",
            {"count": smoothed, "capacity": z.capacity, "pct": pct},
        )
        return events

    def _queue_alert(self, cam: _Camera, z: _Zone, live: _ZoneLive, ts: float, smoothed: float) -> List[dict]:
        return self._hold_alert(
            cam, z, live, ts, smoothed >= LONG_QUEUE_LENGTH, LONG_QUEUE_HOLD_S, "long_queue",
            {"queue_length": smoothed, "threshold": LONG_QUEUE_LENGTH, "lane": z.lane},
        )

    def _hold_alert(self, cam, z, live: _ZoneLive, ts, condition: bool, hold_s: float, kind: str, info: dict) -> List[dict]:
        if condition:
            live.cool_since = None
            if live.hot_since is None:
                live.hot_since = ts
            if not live.alert_open and ts - live.hot_since >= hold_s:
                live.alert_open = True
                ev = _ev(cam, "alert", ts, zone_id=z.id, value=info.get("pct", info.get("queue_length")),
                         payload={"alert_type": kind, "action": "open", "zone_name": z.name,
                                  "since_ts": live.hot_since, **info})
                live.alert_uid = ev["event_uid"]
                return [ev]
            return []
        live.hot_since = None
        if live.alert_open:
            if live.cool_since is None:
                live.cool_since = ts
            if ts - live.cool_since >= ALERT_CLEAR_HOLD_S:
                live.alert_open = False
                live.cool_since = None
                open_uid, live.alert_uid = live.alert_uid, None
                return [_ev(cam, "alert", ts, zone_id=z.id,
                            payload={"alert_type": kind, "action": "close", "zone_name": z.name,
                                     "open_event_uid": open_uid, **info})]
        return []

    def _samples(self, cam: _Camera, ts: float) -> List[dict]:
        events = [_ev(cam, "zone_sample", ts, value=cam.occupancy.value(),
                      payload={"scope": "camera", "raw": cam.occupancy._samples[-1][1] if len(cam.occupancy) else 0})]
        for z in cam.zones:
            if z.zone_type not in ("crowd", "queue", "shelf", "counter"):
                continue
            live = cam.live[z.id]
            val = live.median.value()
            payload = {"zone_type": z.zone_type, "raw": live.raw_count}
            if z.zone_type == "crowd" and z.capacity:
                payload.update(capacity=z.capacity, pct=round(val / z.capacity, 4),
                               level=d.crowd_level_name(live.level_idx))
            events.append(_ev(cam, "zone_sample", ts, zone_id=z.id, value=val, payload=payload))
        return events

    def _expire_stale_tracks(self, cam: _Camera, ts: float) -> List[dict]:
        events: List[dict] = []
        for tid, last in list(cam.last_seen.items()):
            if ts - last >= TRACK_STALE_S:
                events += self._finalize_track(cam, last, tid)
        return events

    # ------------------------------------------------------------- track lost

    def on_track_lost(self, camera_id: str, ts: float, track_id: int) -> list[dict]:
        with self._lock:
            cam = self._cams.get(str(camera_id))
            if cam is None:
                return []
            return self._finalize_track(cam, float(ts), int(track_id))

    def _finalize_track(self, cam: _Camera, ts: float, tid: int) -> List[dict]:
        events: List[dict] = []
        zones = {z.id: z for z in cam.zones}
        for key in [k for k in cam.presences if k[1] == tid]:
            st = cam.presences.pop(key)
            z = zones.get(key[0])
            if z is not None:
                events += self._on_presence_end(cam, z, ts, tid, st)
        for key in [k for k in cam.lines if k[1] == tid]:
            del cam.lines[key]
        for key in [k for k in cam.queue_hist if k[1] == tid]:
            qh = cam.queue_hist.pop(key)
            if qh.qualified:
                events.append(self._abandon(cam, key, qh))
        first = cam.first_seen.pop(tid, None)
        last = cam.last_seen.pop(tid, None)
        gid = cam.global_ids.pop(tid, None)
        cam.last_box.pop(tid, None)
        if first is not None and last is not None and last - first >= TRACK_VISIT_MIN_S:
            events.append(_ev(
                cam, "track_visit", last, track_id=tid, global_id=gid,
                duration_s=round(last - first, 2), payload={"first_seen": first, "last_seen": last},
            ))
        return events

    def on_camera_stopped(self, camera_id, ts: Optional[float] = None) -> list[dict]:
        """Finalise every open presence/track of a camera (stream ended)."""
        with self._lock:
            cam = self._cams.get(str(camera_id))
            if cam is None:
                return []
            return self._flush(cam, float(ts) if ts is not None else (cam.last_ts or 0.0))

    def _flush(self, cam: _Camera, ts: float) -> List[dict]:
        events: List[dict] = []
        for tid in list(cam.last_seen):
            events += self._finalize_track(cam, cam.last_seen.get(tid, ts), tid)
        for key, qh in list(cam.queue_hist.items()):
            del cam.queue_hist[key]
            if qh.qualified:
                events.append(self._abandon(cam, key, qh))
        events += self._emotion_emit(cam, ts, force=True)
        heat = self._heat_emit(cam)
        if heat:
            events.append(heat)
        cam.heat_bucket, cam.heat_last_ts = None, None
        cam.heat_cells, cam.heat_tracks = {}, set()
        if cam.fire.active:
            cam.fire.last_hit_at = min(cam.fire.last_hit_at or ts, ts - d.FIRE_CLEAR_S)
            ev = self._fire_clear(cam, ts)
            if ev:
                events.append(ev)
        cam.presences.clear()
        cam.lines.clear()
        cam.dwell_alerts.clear()
        cam.dwell_gids.clear()
        cam.emotion.clear()
        cam.emotion_dirty.clear()
        cam.last_ts = None
        cam.last_sample_ts = None
        cam.started_ts = None
        cam.arrivals.clear()
        cam.arrival_meta.clear()
        cam.visits.clear()
        cam.faces.clear()
        cam.fire = d.FireState()
        cam.fire_key = None
        for zid, live in list(cam.live.items()):
            z = next((zz for zz in cam.zones if zz.id == zid), None)
            window = d.QUEUE_SMOOTHING_S if z is not None and z.zone_type == "queue" else d.CROWD_SMOOTHING_S
            cam.live[zid] = _ZoneLive(median=d.RollingMedian(window), stats=live.stats)
        cam.occupancy = d.RollingMedian(d.CROWD_SMOOTHING_S)
        return events

    # -------------------------------------------------------------------- fire

    def on_fire(self, camera_id: str, ts: float, detections: list[tuple[tuple, float, str]]) -> list[dict]:
        ts = float(ts)
        with self._lock:
            cam = self._cam(camera_id)
            hit = d.fire_hit(detections or [], cam.frame_wh)
            action = d.fire_step(cam.fire, ts, hit)
            if action is None:
                return []
            return [self._fire_event(cam, ts, action)]

    def _fire_clear(self, cam: _Camera, ts: float) -> Optional[dict]:
        action = d.fire_check_clear(cam.fire, ts)
        return self._fire_event(cam, ts, action) if action else None

    def _fire_event(self, cam: _Camera, ts: float, action: str) -> dict:
        f = cam.fire
        if action == "start" or cam.fire_key is None:
            cam.fire_key = f"{cam.key}:{f.started_at if f.started_at is not None else ts:.3f}"
        return _ev(
            cam, "fire", ts, value=round(f.peak_conf, 4),
            payload={
                "action": action, "incident_key": cam.fire_key, "started_ts": f.started_at,
                "last_hit_ts": f.last_hit_at, "cleared_ts": f.cleared_at, "peak_conf": round(f.peak_conf, 4),
                "class_name": f.class_name, "hits": f.hits, "retriggers": f.retriggers,
                "bbox_xywh": d.xyxy_to_xywh(f.bbox_xyxy) if f.bbox_xyxy else None,
                "frame_wh": list(cam.frame_wh) if cam.frame_wh else None,
            },
        )

    # ------------------------------------------------------------------ emotion

    def on_faces(self, camera_id: str, ts: float, faces: list[tuple[tuple, str, float]]) -> list[dict]:
        ts = float(ts)
        with self._lock:
            cam = self._cam(camera_id)
            bstart = d.bucket_start(ts)
            for xyxy, label, score in faces or []:
                label = str(label or "").strip().lower()
                if label not in d.EMOTION_VALENCE or score is None or float(score) <= 0:
                    continue
                cam.faces.append((ts, label))
                for zid in [None] + self._face_zones(cam, xyxy):
                    key = (zid, bstart)
                    counts = cam.emotion.setdefault(key, {})
                    counts[label] = counts.get(label, 0) + 1
                    cam.emotion_dirty.add(key)
            return self._emotion_emit(cam, ts, force=False)

    def _face_zones(self, cam: _Camera, xyxy: Sequence[float]) -> List[Any]:
        """Zones of the track whose box contains the face centre."""
        if not cam.frame_wh:
            return []
        cx = (float(xyxy[0]) + float(xyxy[2])) / 2.0
        cy = (float(xyxy[1]) + float(xyxy[3])) / 2.0
        best_tid, best_area = None, None
        for tid, (x1, y1, x2, y2) in cam.last_box.items():
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                area = (x2 - x1) * (y2 - y1)
                if best_area is None or area < best_area:
                    best_tid, best_area = tid, area
        if best_tid is None:
            return []
        p = d.normalized_foot_point(cam.last_box[best_tid], cam.frame_wh)
        return [z.id for z in cam.zones if z.zone_type != "entry_line" and d.point_in_polygon(p, z.points)]

    def _emotion_emit(self, cam: _Camera, ts: float, force: bool) -> List[dict]:
        if not cam.emotion_dirty and not cam.emotion:
            return []
        current = d.bucket_start(ts)
        due = force or cam.last_emotion_emit is None or ts - cam.last_emotion_emit >= EMOTION_EMIT_S
        events: List[dict] = []
        for key in list(cam.emotion):
            zid, bstart = key
            closed = bstart < current
            if key in cam.emotion_dirty and (due or closed):
                counts = dict(cam.emotion[key])
                events.append(_ev(
                    cam, "emotion_bucket", bstart, zone_id=zid, value=d.sentiment_from_counts(counts),
                    payload={"bucket_start": bstart, "bucket_s": d.EMOTION_BUCKET_S, "counts": counts,
                             "total": sum(counts.values()), "dominant": d.dominant_label(counts)},
                ))
                cam.emotion_dirty.discard(key)
            if closed and key not in cam.emotion_dirty:
                del cam.emotion[key]
        if due:
            cam.last_emotion_emit = ts
        return events

    # --------------------------------------------------------------------- vibe

    def _trim_windows(self, cam: _Camera, ts: float) -> None:
        while cam.arrivals and cam.arrivals[0][0] < ts - FLOW_WINDOW_S:
            cam.arrivals.popleft()
        if len(cam.arrival_meta) > len(cam.arrivals):
            live = {tid for _, tid in cam.arrivals}
            for tid in [t for t in cam.arrival_meta if t not in live]:
                del cam.arrival_meta[tid]
        while cam.visits and cam.visits[0][0] < ts - VIBE_WINDOW_S:
            cam.visits.popleft()
        while cam.faces and cam.faces[0][0] < ts - VIBE_WINDOW_S:
            cam.faces.popleft()

    def _maybe_vibe(self, ts: float) -> Optional[dict]:
        if self._last_vibe_ts is not None and ts - self._last_vibe_ts < VIBE_INTERVAL_S:
            if ts >= self._last_vibe_ts:
                return None
        self._last_vibe_ts = ts
        vibe = self._compute_vibe(ts)
        self._latest_vibe = vibe
        if vibe is None:
            return None
        return {"type": "vibe_sample", "camera_id": None, "ts": ts, "zone_id": None,
                "value": vibe["score"], "payload": vibe}

    def _compute_vibe(self, ts: float) -> Optional[dict]:
        occ_sum = cap_sum = 0.0
        arrived: Set[tuple] = set()
        flow_span = 0.0
        visits = engaged = 0
        face_counts: Dict[str, int] = {}
        live_cams = []
        for cam in self._cams.values():
            if cam.last_ts is None or ts - cam.last_ts > CAMERA_STALE_S:
                continue
            live_cams.append(cam.key)
            warmed = cam.started_ts is not None and ts - cam.started_ts >= VIBE_WARMUP_S
            for z in cam.zones:
                if warmed and z.zone_type == "crowd" and z.capacity:
                    occ_sum += cam.live[z.id].median.value()
                    cap_sum += z.capacity
            for ats, tid in cam.arrivals:
                if ats < ts - FLOW_WINDOW_S:
                    continue
                am = cam.arrival_meta.get(tid)
                if am is None or (not am.crossed and am.last_ts - am.first_ts < TRACK_VISIT_MIN_S):
                    continue   # detector flicker, not a visitor
                # One person = one arrival, across track-ID switches and across cameras.
                arrived.add(("g", am.gid) if am.gid else ("t", cam.key, tid))
            span = min(FLOW_WINDOW_S, ts - (cam.started_ts or ts))
            flow_span = max(flow_span, span)
            for vts, eng in cam.visits:
                if vts >= ts - VIBE_WINDOW_S:
                    visits += 1
                    engaged += int(eng)
            for fts, label in cam.faces:
                if fts >= ts - VIBE_WINDOW_S:
                    face_counts[label] = face_counts.get(label, 0) + 1
        if not live_cams:
            return None
        arrivals = len(arrived)
        result = d.store_vibe(
            occupancy_pct=(occ_sum / cap_sum) if cap_sum > 0 else None,
            arrivals_per_min=(arrivals / (flow_span / 60.0)) if flow_span >= 60.0 else None,
            engaged_rate=(engaged / visits) if visits else None,
            sentiment=d.sentiment_from_counts(face_counts),
        )
        if result is None:
            return None
        result["inputs"] = {
            "cameras": live_cams, "occupancy": occ_sum, "capacity": cap_sum,
            "arrivals": arrivals, "flow_window_s": round(flow_span, 1),
            "visits": visits, "engaged_visits": engaged, "faces": sum(face_counts.values()),
        }
        result["ts"] = ts
        return result

    def latest_vibe(self) -> Optional[dict]:
        with self._lock:
            return dict(self._latest_vibe) if self._latest_vibe else None

    # ----------------------------------------------------------------- snapshot

    def snapshot(self, camera_id: str) -> dict:
        with self._lock:
            cam = self._cams.get(str(camera_id))
            if cam is None:
                return {"camera_id": str(camera_id), "status": "no_data", "zones": [], "lanes": [],
                        "fire": {"active": False}, "occupancy": None, "active_tracks": 0, "ts": None}
            zones_out = []
            for z in cam.zones:
                live = cam.live[z.id]
                st = live.stats
                med = live.median.value() if len(live.median) else None
                item = {
                    "id": z.id, "name": z.name, "zone_type": z.zone_type, "capacity": z.capacity,
                    "lane": z.lane if z.zone_type in ("queue", "counter") else None,
                    "count": med, "raw_count": live.raw_count,
                    "present": sum(1 for k in cam.presences if k[0] == z.id),
                    "alert_open": live.alert_open,
                    "dwell_alert_s": z.dwell_alert_s,
                    "dwell_alerts_open": sum(1 for k in cam.dwell_alerts if k[0] == z.id),
                }
                if z.zone_type == "crowd":
                    item["pct"] = round(med / z.capacity, 4) if (z.capacity and med is not None) else None
                    item["level"] = d.crowd_level_name(live.level_idx)
                if z.zone_type in ("shelf", "crowd"):
                    item.update(passers=st.passers, visits=st.visits, engaged_visits=st.engaged,
                                median_dwell_s=d.median_or_none(st.dwell))
                if z.zone_type == "entry_line":
                    item.update(count=None, entries=st.crossings_in, exits=st.crossings_out)
                zones_out.append(item)
            lanes_out = []
            for lane in sorted({z.lane for z in cam.zones if z.zone_type in ("queue", "counter")}):
                ls = cam.lanes.get(lane, _LaneStats())
                qz = next((z for z in cam.zones if z.lane == lane and z.zone_type == "queue"), None)
                cz = next((z for z in cam.zones if z.lane == lane and z.zone_type == "counter"), None)
                qlen = cam.live[qz.id].median.value() if qz is not None and len(cam.live[qz.id].median) else None
                lanes_out.append({
                    "lane": lane, "queue_zone_id": qz.id if qz else None, "counter_zone_id": cz.id if cz else None,
                    "queue_length": qlen,
                    "waiting": sum(1 for (ln, _), qh in cam.queue_hist.items() if ln == lane and not qh.ended),
                    "median_wait_s": d.median_or_none(w for _, w in ls.waits),
                    "median_service_s": d.median_or_none(s for _, s in ls.services),
                    "served": len(ls.services), "abandoned": len(ls.abandons),
                })
            f = cam.fire
            fire = {"active": f.active, "incident_key": cam.fire_key if f.active else None,
                    "started_ts": f.started_at if f.active else None,
                    "last_hit_ts": f.last_hit_at, "peak_conf": round(f.peak_conf, 4) if f.active else None,
                    "class_name": f.class_name if f.active else None,
                    "recent_hits": int(sum(f.window)), "window": d.FIRE_WINDOW}
            current_bucket = d.bucket_start(cam.last_ts) if cam.last_ts else None
            emo = cam.emotion.get((None, current_bucket), {}) if current_bucket is not None else {}
            return {
                "camera_id": cam.key,
                "status": "ok" if cam.last_ts is not None else "no_data",
                "ts": cam.last_ts,
                "frame_wh": list(cam.frame_wh) if cam.frame_wh else None,
                "active_tracks": len(cam.last_seen),
                "occupancy": cam.occupancy.value() if len(cam.occupancy) else None,
                "zones": zones_out,
                "lanes": lanes_out,
                "fire": fire,
                "emotion": {"bucket_start": current_bucket, "counts": dict(emo),
                            "sentiment": d.sentiment_from_counts(emo)},
            }

    def camera_ids(self) -> List[str]:
        with self._lock:
            return list(self._cams)

    def forget_camera(self, camera_id) -> None:
        with self._lock:
            self._cams.pop(str(camera_id), None)


_engine: Optional[ZoneAnalyticsEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> ZoneAnalyticsEngine:
    """Process-wide engine singleton (pipeline writes, routers read)."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = ZoneAnalyticsEngine()
    return _engine


def iter_events(*groups: Iterable[dict]) -> List[dict]:
    out: List[dict] = []
    for g in groups:
        out.extend(g or [])
    return out
