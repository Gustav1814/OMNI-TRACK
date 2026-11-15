"""
OmniTrack AI — Track Coordinator: global identities across cameras.

Every confirmed local track (camera, track_id) becomes a *tracklet*. While it
is visible, crops that pass the Re-ID quality gate are embedded and the best
``top_k`` (by quality) are kept; the tracklet feature is their L2-normalised
mean. Once a tracklet is ready it is matched, together with every other
ready tracklet, against the *recently lost* identities in one Hungarian
assignment:

    cost[i, j] = 1 - sim(tracklet_i, identity_j)
    feasible   = sim >= T_match  and  topology/transit gate allows
                 (identity_j last seen on camera A at t_A,
                  tracklet_i first seen on camera B at t_B)

Unmatched tracklets get a new identity. A global ID never changes during a
local track: once assigned, the tracklet keeps it until ``track_lost``.

Identity update: a tracklet's final feature is added to its identity as a new
view only if ``sim >= T_update`` (> T_match), so a wrong match cannot pull the
identity towards someone else. Each identity keeps <= ``max_views`` diverse
views.

Topology (``set_topology``): links ``from -> to`` with ``[min_s, max_s]``
transit and an ``overlapping`` flag. With no links configured, any camera may
follow any other within ``[0, default_max_transit_s]`` (a sane default for an
unmapped store). Once links exist they are authoritative: a pair with no link
is not allowed. Same-camera re-entry is always allowed within
``reentry_max_s``.

Overlapping cameras may see the same person at the same time. When both
cameras have a floor-plane homography (``set_calibration``), a pending
tracklet whose foot point stays within ``fusion_distance_m`` of an assigned
track on the other camera for ``fusion_min_s`` seconds, and whose appearance
agrees (``>= fusion_min_sim``), takes that track's global ID (fusion). With no
calibration, fusion is off and overlapping cameras match on appearance only.

All state is in-process (one coordinator per backend process); assignments
are mirrored to the event bus (and from there to the Redis stream
``tracks.assignments``) for other consumers.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from loguru import logger

from app.ai.reid import EMBEDDING_DIM, IdentityGallery, l2n

try:
    from scipy.optimize import linear_sum_assignment
except Exception:  # pragma: no cover - scipy is a hard dependency
    linear_sum_assignment = None

INFEASIBLE = 1e6


# ─────────────────────────────────────────────────────────────────────────────
# Topology / transit gating
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CameraLink:
    from_camera: int
    to_camera: int
    min_s: float = 0.0
    max_s: float = 300.0
    overlapping: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "from_camera": self.from_camera,
            "to_camera": self.to_camera,
            "min_s": self.min_s,
            "max_s": self.max_s,
            "overlapping": self.overlapping,
        }


def parse_links(raw: Iterable[dict]) -> List[CameraLink]:
    """Accept ``{from|from_camera, to|to_camera, min_s, max_s, overlapping, bidirectional}`` dicts."""
    links: List[CameraLink] = []
    for item in raw or []:
        a = int(item.get("from_camera", item.get("from")))
        b = int(item.get("to_camera", item.get("to")))
        lo = float(item.get("min_s", 0.0))
        hi = float(item.get("max_s", 300.0))
        if hi < lo:
            raise ValueError(f"link {a}->{b}: max_s < min_s")
        ov = bool(item.get("overlapping", False))
        links.append(CameraLink(a, b, lo, hi, ov))
        if item.get("bidirectional", False) and a != b:
            links.append(CameraLink(b, a, lo, hi, ov))
    return links


class Topology:
    def __init__(self, links: Sequence[CameraLink] = (), default_max_transit_s: float = 300.0, reentry_max_s: float = 1800.0):
        self.default_max_transit_s = float(default_max_transit_s)
        self.reentry_max_s = float(reentry_max_s)
        self.links: Dict[Tuple[int, int], CameraLink] = {(lk.from_camera, lk.to_camera): lk for lk in links}

    @property
    def configured(self) -> bool:
        return bool(self.links)

    def overlapping(self, a: int, b: int) -> bool:
        lk = self.links.get((a, b)) or self.links.get((b, a))
        return bool(lk and lk.overlapping)

    def allowed(self, from_cam: Optional[int], to_cam: int, dt: float) -> bool:
        """Can an identity last seen on ``from_cam`` appear on ``to_cam`` ``dt`` seconds later?"""
        if from_cam is None:  # restored identity with unknown camera
            return 0.0 <= dt <= self.default_max_transit_s
        if from_cam == to_cam:
            return 0.0 <= dt <= self.reentry_max_s
        lk = self.links.get((from_cam, to_cam))
        if lk is not None:
            if lk.overlapping:
                return dt <= lk.max_s  # may be simultaneous (dt < 0 when still visible on from_cam)
            return lk.min_s <= dt <= lk.max_s
        if self.configured:
            return False
        return 0.0 <= dt <= self.default_max_transit_s

    def max_window_s(self) -> float:
        hi = [lk.max_s for lk in self.links.values()]
        return max([self.default_max_transit_s, self.reentry_max_s] + hi)

    def as_list(self) -> List[Dict[str, object]]:
        return [lk.as_dict() for lk in self.links.values()]


def floor_point(H: np.ndarray, foot_xy: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    p = H @ np.array([foot_xy[0], foot_xy[1], 1.0], np.float64)
    if abs(p[2]) < 1e-9:
        return None
    return (float(p[0] / p[2]), float(p[1] / p[2]))


# ─────────────────────────────────────────────────────────────────────────────
# Tracklets
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Tracklet:
    camera_id: int
    track_id: int
    first_ts: float
    last_ts: float
    top_k: int
    crops: List[Tuple[float, np.ndarray]] = field(default_factory=list)  # (quality score, embedding)
    n_embedded: int = 0
    first_crop_ts: Optional[float] = None
    global_id: Optional[str] = None
    similarity: Optional[float] = None
    assigned_ts: Optional[float] = None
    match_reason: Optional[str] = None
    created_identity: bool = False
    superseded: bool = False  # its identity was handed to a newer tracklet after this one went unseen
    last_bbox: Optional[List[float]] = None
    floor: Deque[Tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=64))  # (ts, X, Y)
    last_confidence: float = 0.0

    def add_crop(self, score: float, emb: np.ndarray, ts: float) -> None:
        self.n_embedded += 1
        if self.first_crop_ts is None:
            self.first_crop_ts = ts
        self.crops.append((float(score), l2n(emb)))
        if len(self.crops) > self.top_k:
            self.crops.sort(key=lambda c: c[0], reverse=True)
            del self.crops[self.top_k:]

    def feature(self) -> Optional[np.ndarray]:
        if not self.crops:
            return None
        return l2n(np.mean(np.stack([e for _, e in self.crops]), axis=0))

    @property
    def n_crops(self) -> int:
        return len(self.crops)


@dataclass
class CoordinatorConfig:
    t_match: float = 0.55
    t_update: float = 0.65
    top_k: int = 5
    min_crops: int = 2
    max_wait_s: float = 3.0
    max_views: int = 8
    max_identities: int = 2000
    default_max_transit_s: float = 300.0
    reentry_max_s: float = 1800.0
    fusion_distance_m: float = 0.75
    fusion_min_s: float = 1.0
    fusion_min_sim: float = 0.5
    # A tracklet that appears on the camera an identity was expected at, inside the link's transit
    # window, gets this cost discount in the Hungarian step, so expected handoffs are matched first.
    # It never lowers t_match: the calibrated similarity gate still decides feasibility.
    handoff_bonus: float = 0.1
    refresh_every_s: float = 1.0  # embed an already-full tracklet at most this often (better crops replace worse)


class TrackCoordinator:
    """Thread-safe owner of the identity gallery and all global-ID decisions."""

    def __init__(self, cfg: Optional[CoordinatorConfig] = None, topology: Optional[Topology] = None):
        self.cfg = cfg or CoordinatorConfig()
        self.gallery = IdentityGallery(max_identities=self.cfg.max_identities, max_views=self.cfg.max_views)
        self.topology = topology or Topology(
            default_max_transit_s=self.cfg.default_max_transit_s, reentry_max_s=self.cfg.reentry_max_s
        )
        self._calib: Dict[int, np.ndarray] = {}
        self._tracklets: Dict[Tuple[int, int], Tracklet] = {}
        self._last_embed_ts: Dict[Tuple[int, int], float] = {}
        self._pending_vectors: List[Dict[str, object]] = []
        self._dirty_identities: set[str] = set()
        self._expected: List[Dict[str, object]] = []
        self._lock = threading.RLock()
        self.stats = {"assigned_existing": 0, "created": 0, "fused": 0, "views_added": 0, "updates_rejected": 0, "handoffs": 0}

    # ── configuration ──────────────────────────────────────────────
    def set_topology(self, links: Sequence[CameraLink]) -> None:
        with self._lock:
            self.topology = Topology(links, self.cfg.default_max_transit_s, self.cfg.reentry_max_s)
        logger.info(f"Re-ID topology: {len(links)} link(s)" if links else "Re-ID topology: none configured (default gating)")

    def set_calibration(self, camera_id: int, homography: Optional[Sequence[Sequence[float]]]) -> None:
        with self._lock:
            if homography is None:
                self._calib.pop(int(camera_id), None)
                return
            H = np.asarray(homography, np.float64).reshape(3, 3)
            if not np.isfinite(H).all() or abs(np.linalg.det(H)) < 1e-12:
                raise ValueError("homography must be a finite, invertible 3x3 matrix")
            self._calib[int(camera_id)] = H

    def calibrated_cameras(self) -> List[int]:
        with self._lock:
            return sorted(self._calib)

    # ── warm start ─────────────────────────────────────────────────
    def warm(self, rows: Sequence[Dict[str, object]]) -> Dict[str, int]:
        """Restore identities from pgvector rows ``{global_id, vector, timestamp, camera_id}``."""
        n_rows = 0
        with self._lock:
            by_gid: Dict[str, List[Dict[str, object]]] = {}
            for r in rows or []:
                gid, vec = r.get("global_id"), r.get("vector")
                if not gid or vec is None:
                    continue
                v = np.asarray(vec, np.float32).reshape(-1)
                if v.shape[0] != EMBEDDING_DIM or not np.isfinite(v).all():
                    continue
                by_gid.setdefault(str(gid), []).append({**r, "vector": l2n(v)})
            for gid, items in by_gid.items():
                items.sort(key=lambda r: float(r.get("timestamp") or 0.0))
                ident = self.gallery.get(gid)
                last = items[-1]
                ts_last = float(last.get("timestamp") or 0.0)
                cam = last.get("camera_id")
                if ident is None:
                    ident = self.gallery.create(
                        items[0]["vector"], float(items[0].get("timestamp") or 0.0),
                        int(cam) if cam is not None else None, gid=gid,
                    )
                    ident.restored = True
                for r in items[1:]:
                    ident.add_view(r["vector"], self.gallery.max_views)
                ident.touch(ts_last, int(cam) if cam is not None else None)
                n_rows += len(items)
            self.gallery.seed_counter(by_gid.keys())
            self.gallery.evict()
            return {"rows": n_rows, "identities": len(by_gid), "counter": self.gallery.counter}

    def seed_counter(self, gids: Iterable[str]) -> None:
        with self._lock:
            self.gallery.seed_counter(gids)

    # ── per frame ──────────────────────────────────────────────────
    def wants_embedding(self, camera_id: int, track_id: int, ts: float) -> bool:
        """Should a quality crop of this track be embedded now?"""
        key = (int(camera_id), int(track_id))
        with self._lock:
            tl = self._tracklets.get(key)
            if tl is None or tl.n_crops < self.cfg.top_k:
                return True
            return ts - self._last_embed_ts.get(key, -1e9) >= self.cfg.refresh_every_s

    def observe(
        self,
        camera_id: int,
        ts: float,
        tracks: Sequence[Tuple[int, List[float], float, Tuple[float, float]]],
        embeddings: Dict[int, Tuple[float, np.ndarray]],
    ) -> None:
        """Record this frame's confirmed tracks ``(track_id, bbox_xywh, conf, foot_xy)`` and new embeddings."""
        cam = int(camera_id)
        with self._lock:
            H = self._calib.get(cam)
            for tid, bbox, conf, foot in tracks:
                key = (cam, int(tid))
                tl = self._tracklets.get(key)
                if tl is None:
                    tl = Tracklet(cam, int(tid), first_ts=ts, last_ts=ts, top_k=self.cfg.top_k)
                    self._tracklets[key] = tl
                tl.last_ts = ts
                tl.last_bbox = list(bbox)
                tl.last_confidence = float(conf)
                if H is not None:
                    fp = floor_point(H, foot)
                    if fp is not None:
                        tl.floor.append((ts, fp[0], fp[1]))
                emb = embeddings.get(int(tid))
                if emb is not None:
                    tl.add_crop(emb[0], emb[1], ts)
                    self._last_embed_ts[key] = ts
                if tl.global_id:
                    ident = self.gallery.get(tl.global_id)
                    if ident is not None:
                        ident.touch(ts, cam, tl.last_bbox)
                        if tl.floor:
                            ident.last_foot_xy = (tl.floor[-1][1], tl.floor[-1][2])

    def add_embeddings(self, camera_id: int, ts: float, embeddings: Dict[int, Tuple[float, np.ndarray]]) -> int:
        """Attach crops embedded off the frame loop (taken at ``ts``). Tracks that ended meanwhile are skipped."""
        cam, n = int(camera_id), 0
        with self._lock:
            for tid, (score, feat) in embeddings.items():
                key = (cam, int(tid))
                tl = self._tracklets.get(key)
                if tl is None:
                    continue
                tl.add_crop(score, feat, ts)
                self._last_embed_ts[key] = ts
                n += 1
        return n

    def _ready(self, tl: Tracklet, ts: float, force: bool = False) -> bool:
        if tl.global_id is not None or tl.n_crops == 0:
            return False
        if tl.n_crops >= self.cfg.top_k:
            return True
        first = tl.first_crop_ts if tl.first_crop_ts is not None else ts
        if tl.n_crops >= self.cfg.min_crops and (force or ts - first >= self.cfg.max_wait_s):
            return True
        return False

    def _active_gids(self) -> Dict[str, Tracklet]:
        return {tl.global_id: tl for tl in self._tracklets.values() if tl.global_id}

    def _fusion_partner(self, tl: Tracklet, active: Dict[str, Tracklet], feat: np.ndarray) -> Optional[Tuple[str, float]]:
        """An assigned track on another calibrated camera that has stood where ``tl`` stands for ``fusion_min_s``."""
        if tl.camera_id not in self._calib or len(tl.floor) < 2:
            return None
        best: Optional[Tuple[str, float]] = None
        for gid, other in active.items():
            if other.camera_id == tl.camera_id or other.camera_id not in self._calib or not other.floor:
                continue
            if self.topology.configured and not self.topology.overlapping(tl.camera_id, other.camera_id):
                continue
            o_ts = np.array([p[0] for p in other.floor])
            close_since = None
            ok = True
            for ts, x, y in reversed(tl.floor):
                j = int(np.argmin(np.abs(o_ts - ts)))
                if abs(o_ts[j] - ts) > 0.5:
                    ok = False
                    break
                _, ox, oy = other.floor[j]
                if np.hypot(ox - x, oy - y) > self.cfg.fusion_distance_m:
                    break
                close_since = ts
            if not ok or close_since is None or tl.floor[-1][0] - close_since < self.cfg.fusion_min_s:
                continue
            ident = self.gallery.get(gid)
            if ident is None:
                continue
            sim = ident.similarity(feat)
            if sim >= self.cfg.fusion_min_sim and (best is None or sim > best[1]):
                best = (gid, sim)
        return best

    def step(self, ts: float, force_keys: Iterable[Tuple[int, int]] = ()) -> List[Dict[str, object]]:
        """Assign global IDs to every ready tracklet. Returns assignment events."""
        forced = set(force_keys)
        events: List[Dict[str, object]] = []
        with self._lock:
            pending = [tl for key, tl in self._tracklets.items() if self._ready(tl, ts, force=key in forced)]
            if not pending:
                return events
            active = self._active_gids()

            # 1. Fusion with a co-located track on an overlapping, calibrated camera.
            rest: List[Tracklet] = []
            for tl in pending:
                feat = tl.feature()
                partner = self._fusion_partner(tl, active, feat) if self._calib else None
                if partner is not None:
                    gid, sim = partner
                    events.append(self._assign(tl, gid, sim, ts, "fusion"))
                    self.stats["fused"] += 1
                else:
                    rest.append(tl)
            if not rest:
                return events

            # 2. Hungarian against identities not visible right now (+ overlapping visible ones).
            #    An identity whose live tracklet went unseen before this tracklet appeared is a
            #    candidate too: that is a local track fragment (occlusion, ByteTrack re-init).
            feats = np.stack([tl.feature() for tl in rest])
            newest = max(tl.first_ts for tl in rest)
            cand: List[str] = []
            for ident in self.gallery.identities():
                owner = active.get(ident.global_id)
                if owner is None or owner.last_ts < newest or any(
                    self.topology.overlapping(owner.camera_id, tl.camera_id) for tl in rest
                ):
                    cand.append(ident.global_id)
            cost = np.full((len(rest), max(1, len(cand))), INFEASIBLE, np.float64)
            sims = np.full(cost.shape, -1.0, np.float32)
            if cand:
                sims = self.gallery.similarity_matrix(feats, cand)
                for j, gid in enumerate(cand):
                    ident = self.gallery.get(gid)
                    owner = active.get(gid)
                    for i, tl in enumerate(rest):
                        if sims[i, j] < self.cfg.t_match:
                            continue
                        if owner is not None and owner.last_ts >= tl.first_ts:
                            # Simultaneously visible: only on a declared overlapping pair, never same camera.
                            if owner.camera_id == tl.camera_id or not self.topology.overlapping(owner.camera_id, tl.camera_id):
                                continue
                        else:
                            dt = tl.first_ts - ident.last_seen
                            if not self.topology.allowed(ident.last_camera_id, tl.camera_id, dt):
                                continue
                        cost[i, j] = 1.0 - float(sims[i, j])
                        if self._is_expected(gid, tl.camera_id, tl.first_ts):
                            cost[i, j] -= self.cfg.handoff_bonus
            matched: Dict[int, Tuple[str, float]] = {}
            if cand and linear_sum_assignment is not None:
                rows, cols = linear_sum_assignment(cost)
                for i, j in zip(rows, cols):
                    if cost[i, j] < INFEASIBLE:
                        matched[int(i)] = (cand[int(j)], float(sims[i, j]))
            for i, tl in enumerate(rest):
                if i in matched:
                    gid, sim = matched[i]
                    owner = active.get(gid)
                    if owner is not None and owner.last_ts < tl.first_ts:
                        owner.global_id, owner.superseded = None, True
                    handoff = self._is_expected(gid, tl.camera_id, tl.first_ts)
                    events.append(self._assign(tl, gid, sim, ts, "handoff" if handoff else "match"))
                    self.stats["assigned_existing"] += 1
                    if handoff:
                        self.stats["handoffs"] += 1
                else:
                    ident = self.gallery.create(feats[i], tl.first_ts, tl.camera_id, tl.last_bbox)
                    self.gallery.evict(protected=self._active_gids().keys())
                    tl.created_identity = True
                    self._queue_vector(tl, ident.global_id, feats[i], ts)
                    events.append(self._assign(tl, ident.global_id, None, ts, "new"))
                    self.stats["created"] += 1
        return events

    def _is_expected(self, gid: str, camera_id: int, ts: float) -> bool:
        return any(
            e["global_id"] == gid and e["to_camera"] == camera_id and e["earliest"] <= ts <= e["latest"]
            for e in self._expected
        )

    def _assign(self, tl: Tracklet, gid: str, sim: Optional[float], ts: float, reason: str) -> Dict[str, object]:
        tl.global_id = gid
        tl.similarity = sim
        tl.assigned_ts = ts
        tl.match_reason = reason
        ident = self.gallery.get(gid)
        if ident is not None:
            ident.touch(ts, tl.camera_id, tl.last_bbox)
        self._expected = [e for e in self._expected if e["global_id"] != gid]
        self._dirty_identities.add(gid)
        return {
            "type": "reid_assigned",
            "camera_id": tl.camera_id,
            "track_id": tl.track_id,
            "global_id": gid,
            "similarity": None if sim is None else round(float(sim), 4),
            "reason": reason,
            "crops": tl.n_crops,
            "ts": ts,
        }

    def _queue_vector(self, tl: Tracklet, gid: str, vec: np.ndarray, ts: float) -> None:
        self._pending_vectors.append({
            "camera_id": tl.camera_id,
            "track_id": tl.track_id,
            "global_id": gid,
            "embedding": [float(x) for x in vec],
            "confidence": float(tl.last_confidence),
            "timestamp": float(ts),
        })
        self._dirty_identities.add(gid)

    def on_track_lost(self, camera_id: int, track_id: int, ts: float) -> List[Dict[str, object]]:
        """Finalise a tracklet: last-chance assignment, guarded identity update, handoff expectations."""
        key = (int(camera_id), int(track_id))
        events: List[Dict[str, object]] = []
        with self._lock:
            tl = self._tracklets.get(key)
            if tl is None:
                return events
            if tl.global_id is None and tl.n_crops >= self.cfg.min_crops:
                events += self.step(ts, force_keys=[key])
            tl = self._tracklets.pop(key)
            self._last_embed_ts.pop(key, None)
            gid = tl.global_id
            ident = self.gallery.get(gid) if gid else None
            if ident is not None:
                feat = tl.feature()
                if feat is not None and not (tl.created_identity and tl.n_crops <= 1):
                    sim = ident.similarity(feat)
                    if sim >= self.cfg.t_update:
                        if ident.add_view(feat, self.gallery.max_views):
                            ident.n_updates += 1
                            self.stats["views_added"] += 1
                            self._queue_vector(tl, gid, feat, ts)
                    else:
                        self.stats["updates_rejected"] += 1
                ident.touch(tl.last_ts, tl.camera_id, tl.last_bbox)
                # Expected arrivals on linked (non-overlapping) cameras: the handoff window.
                for (a, b), lk in self.topology.links.items():
                    if a == tl.camera_id and not lk.overlapping:
                        self._expected.append({
                            "global_id": gid, "from_camera": a, "to_camera": b,
                            "earliest": tl.last_ts + lk.min_s, "latest": tl.last_ts + lk.max_s,
                        })
                events.append({
                    "type": "reid_track_ended", "camera_id": tl.camera_id, "track_id": tl.track_id,
                    "global_id": gid, "ts": ts, "duration_s": round(tl.last_ts - tl.first_ts, 2),
                })
        return events

    def drop_camera(self, camera_id: int, ts: float) -> List[Dict[str, object]]:
        with self._lock:
            keys = [k for k in self._tracklets if k[0] == int(camera_id)]
        events: List[Dict[str, object]] = []
        for k in keys:
            events += self.on_track_lost(k[0], k[1], ts)
        return events

    def maintain(self, ts: float) -> None:
        """Expire handoff windows and identities older than every gating window."""
        with self._lock:
            self._expected = [e for e in self._expected if e["latest"] >= ts]
            cutoff = ts - self.topology.max_window_s()
            self.gallery.prune_older_than(cutoff, protected=self._active_gids().keys())

    # ── read side ──────────────────────────────────────────────────
    def global_id(self, camera_id: int, track_id: int) -> Optional[str]:
        tl = self._tracklets.get((int(camera_id), int(track_id)))
        return tl.global_id if tl else None

    def tracklet_info(self, camera_id: int, track_id: int) -> Optional[Dict[str, object]]:
        tl = self._tracklets.get((int(camera_id), int(track_id)))
        if tl is None:
            return None
        return {"global_id": tl.global_id, "similarity": tl.similarity, "reason": tl.match_reason, "crops": tl.n_crops}

    def drain_vectors(self) -> List[Dict[str, object]]:
        with self._lock:
            out, self._pending_vectors = self._pending_vectors, []
            return out

    def drain_dirty_identities(self) -> List[Dict[str, object]]:
        """Identity rows (centroid etc.) changed since the last drain, for the ``reid_identities`` table."""
        with self._lock:
            out = []
            for gid in self._dirty_identities:
                ident = self.gallery.get(gid)
                if ident is None or not ident.views:
                    continue
                out.append({
                    "global_id": gid,
                    "centroid": [float(x) for x in ident.centroid],
                    "n_views": len(ident.views),
                    "first_seen": ident.first_seen,
                    "last_seen": ident.last_seen,
                    "last_camera_id": ident.last_camera_id,
                })
            self._dirty_identities.clear()
            return out

    def active(self) -> List[Dict[str, object]]:
        with self._lock:
            return [
                {
                    "global_id": tl.global_id, "camera_id": tl.camera_id, "track_id": tl.track_id,
                    "similarity": tl.similarity, "reason": tl.match_reason, "last_seen": tl.last_ts,
                    "bbox": tl.last_bbox,
                }
                for tl in self._tracklets.values() if tl.global_id
            ]

    def identity(self, global_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            ident = self.gallery.get(global_id)
            return ident.summary() if ident is not None else None

    def identities(self, limit: int = 100) -> List[Dict[str, object]]:
        """Most recently seen in-memory identities (summaries, newest first)."""
        with self._lock:
            items = sorted(self.gallery.identities(), key=lambda i: i.last_seen, reverse=True)
            return [{**i.summary(), "last_bbox": i.last_bbox} for i in items[: max(0, int(limit))]]

    def similar_to(self, global_id: str, top_k: int = 10, threshold: float = 0.0) -> Optional[List[Dict[str, object]]]:
        """Identities whose views look like ``global_id``'s centroid; None if it is not in memory."""
        with self._lock:
            ident = self.gallery.get(global_id)
            q = ident.centroid if ident is not None else None
            if q is None:
                return None
            out = []
            for hit in self.gallery.search(q, top_k=top_k, threshold=threshold, exclude=(global_id,)):
                other = self.gallery.get(str(hit["id"]))
                if other is not None:
                    out.append({**other.summary(), "last_bbox": other.last_bbox, "similarity": hit["similarity"]})
            return out

    def expected_arrivals(self) -> List[Dict[str, object]]:
        with self._lock:
            return [dict(e) for e in self._expected]

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            n_pending = sum(1 for tl in self._tracklets.values() if tl.global_id is None)
            return {
                **self.gallery.stats(),
                "tracklets": len(self._tracklets),
                "tracklets_pending": n_pending,
                "t_match": self.cfg.t_match,
                "t_update": self.cfg.t_update,
                "topology_links": len(self.topology.links),
                "calibrated_cameras": sorted(self._calib),
                "fusion": "on" if len(self._calib) >= 2 else "off (needs 2+ calibrated cameras)",
                "expected_arrivals": len(self._expected),
                **self.stats,
            }
