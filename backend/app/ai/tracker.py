"""
OmniTrack AI — per-camera person tracker (ByteTrack + track lifecycle).

One instance per camera. Detections come straight from the single person
detector (``conf`` already as low as 0.1); ByteTrack decides which boxes become
tracks. Each returned ByteTrack row carries the index of the detection it was
built from (``idx``), so track IDs are attached without any secondary IoU
matching.

Lifecycle (on top of ByteTrack's own state):

* ``tentative``  – the track exists but has fewer than ``min_hits`` matched
  updates. It is never drawn, counted, Re-ID'd or analysed.
* ``confirmed``  – at least ``min_hits`` matched updates. Emits
  ``track_confirmed`` once, then ``track_updated`` on every matched update.
* ``occluded``   – ByteTrack lost it this frame but it is still inside the
  buffer (``buffer_s`` seconds). Not drawn, not counted; it can come back with
  the same ID.
* ``lost``       – it left ByteTrack's buffer. Emits ``track_lost`` (for
  confirmed tracks only) and every per-track structure is dropped.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

try:
    from ultralytics.trackers.byte_tracker import BYTETracker

    BYTETRACK_AVAILABLE = True
except Exception as _e:  # pragma: no cover - ultralytics is a hard requirement in prod
    BYTETracker = None
    BYTETRACK_AVAILABLE = False
    logger.error(f"ByteTrack unavailable ({_e}); person tracking is disabled.")


TENTATIVE = "tentative"
CONFIRMED = "confirmed"
OCCLUDED = "occluded"
LOST = "lost"


@dataclass
class Track:
    """State of one track on one camera. Boxes are pixel ``x1, y1, x2, y2``."""

    track_id: int
    xyxy: Tuple[float, float, float, float]
    confidence: float
    state: str = TENTATIVE
    hits: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    first_frame: int = 0
    last_frame: int = 0
    det_index: int = -1  # index into this frame's detection array, -1 when not matched this frame
    class_name: str = "person"

    @property
    def bbox_xywh(self) -> List[float]:
        """Top-left ``[x, y, w, h]`` (the format used across the API and DB)."""
        x1, y1, x2, y2 = self.xyxy
        return [float(x1), float(y1), float(x2 - x1), float(y2 - y1)]

    @property
    def foot_point(self) -> Tuple[float, float]:
        x1, _y1, x2, y2 = self.xyxy
        return (float((x1 + x2) / 2.0), float(y2))

    @property
    def is_confirmed(self) -> bool:
        return self.state == CONFIRMED

    def snapshot(self) -> Dict[str, object]:
        return {
            "track_id": self.track_id,
            "bbox": [round(v, 1) for v in self.bbox_xywh],
            "xyxy": [round(float(v), 1) for v in self.xyxy],
            "confidence": round(float(self.confidence), 3),
            "state": self.state,
            "hits": self.hits,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "class_name": self.class_name,
        }


@dataclass
class TrackEvent:
    type: str  # track_confirmed | track_updated | track_lost
    track_id: int
    ts: float
    track: Dict[str, object] = field(default_factory=dict)


@dataclass
class TrackerOutput:
    tracks: List[Track]  # every track matched this frame (tentative + confirmed)
    events: List[TrackEvent]
    det_to_track: np.ndarray  # (N,) track id per detection, -1 when unassigned

    @property
    def confirmed(self) -> List[Track]:
        return [t for t in self.tracks if t.state == CONFIRMED]


class _Dets:
    """Minimal Results-like adapter accepted by ``BYTETracker.update``."""

    def __init__(self, xyxy: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(conf, dtype=np.float32).reshape(-1)
        self.cls = np.asarray(cls, dtype=np.float32).reshape(-1)
        wh = self.xyxy[:, 2:4] - self.xyxy[:, 0:2]
        self.xywh = np.concatenate([self.xyxy[:, 0:2] + wh / 2.0, wh], axis=1).astype(np.float32)

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, idx) -> "_Dets":
        return _Dets(self.xyxy[idx], self.conf[idx], self.cls[idx])


def bytetrack_args(
    fps: float,
    track_high_thresh: float = 0.5,
    track_low_thresh: float = 0.1,
    new_track_thresh: float = 0.6,
    match_thresh: float = 0.8,
    buffer_s: float = 3.0,
    fuse_score: bool = True,
) -> SimpleNamespace:
    fps = max(0.5, float(fps))
    return SimpleNamespace(
        tracker_type="bytetrack",
        track_high_thresh=float(track_high_thresh),
        track_low_thresh=float(track_low_thresh),
        new_track_thresh=float(new_track_thresh),
        match_thresh=float(match_thresh),
        track_buffer=max(1, int(round(buffer_s * fps))),
        fuse_score=bool(fuse_score),
        frame_rate=int(round(fps)),
    )


class PersonTracker:
    """ByteTrack for one camera, plus the tentative/confirmed/lost lifecycle."""

    def __init__(
        self,
        fps: float,
        track_high_thresh: float = 0.5,
        track_low_thresh: float = 0.1,
        new_track_thresh: float = 0.6,
        match_thresh: float = 0.8,
        buffer_s: float = 3.0,
        min_hits: int = 3,
        fuse_score: bool = True,
    ):
        if not BYTETRACK_AVAILABLE:
            raise RuntimeError("ultralytics ByteTrack is not installed")
        self.min_hits = max(1, int(min_hits))
        self.buffer_s = float(buffer_s)
        self._cfg = dict(
            track_high_thresh=track_high_thresh,
            track_low_thresh=track_low_thresh,
            new_track_thresh=new_track_thresh,
            match_thresh=match_thresh,
            buffer_s=buffer_s,
            fuse_score=fuse_score,
        )
        self.fps = max(0.5, float(fps))
        self._bt = self._make(self.fps)
        self._tracks: Dict[int, Track] = {}
        self._frame = 0

    # ── construction ──────────────────────────────────────────────
    def _make(self, fps: float):
        args = bytetrack_args(fps, **self._cfg)
        # Older ultralytics took ``frame_rate`` as a ctor arg and derived the buffer from it.
        if "frame_rate" in inspect.signature(BYTETracker.__init__).parameters:
            return BYTETracker(args, frame_rate=args.frame_rate)
        return BYTETracker(args)

    @property
    def track_buffer_frames(self) -> int:
        return int(self._bt.max_frames_lost if hasattr(self._bt, "max_frames_lost") else self._bt.max_time_lost)

    def set_fps(self, fps: float) -> None:
        """Keep the lost-track buffer at ``buffer_s`` seconds when the real processing rate changes."""
        fps = max(0.5, float(fps))
        if abs(fps - self.fps) / self.fps < 0.15:
            return
        self.fps = fps
        frames = max(1, int(round(self.buffer_s * fps)))
        self._bt.args.track_buffer = frames
        self._bt.args.frame_rate = int(round(fps))
        if hasattr(self._bt, "max_frames_lost"):
            self._bt.max_frames_lost = frames
        else:  # pragma: no cover - older ultralytics
            self._bt.max_time_lost = frames

    def reset(self) -> List[TrackEvent]:
        """Drop every track (camera restart). Returns ``track_lost`` for confirmed tracks."""
        events = [
            TrackEvent("track_lost", t.track_id, t.last_ts, t.snapshot())
            for t in self._tracks.values()
            if t.state in (CONFIRMED, OCCLUDED) and t.hits >= self.min_hits
        ]
        self._tracks.clear()
        self._bt = self._make(self.fps)
        self._frame = 0
        return events

    # ── per frame ─────────────────────────────────────────────────
    def update(self, xyxy: np.ndarray, conf: np.ndarray, ts: float) -> TrackerOutput:
        """Advance the tracker by one processed frame.

        ``xyxy`` (N, 4) and ``conf`` (N,) are the person detections for this frame
        (all of them, including low-confidence ones for ByteTrack's second stage).
        """
        self._frame += 1
        xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        conf = np.asarray(conf, dtype=np.float32).reshape(-1)
        n = len(conf)
        det_to_track = np.full(n, -1, dtype=np.int64)
        rows = self._bt.update(_Dets(xyxy, conf, np.zeros(n, np.float32)))
        events: List[TrackEvent] = []
        matched: List[Track] = []
        seen: set[int] = set()

        for row in np.asarray(rows).reshape(-1, 8) if len(rows) else []:
            tid = int(row[4])
            idx = int(row[7])
            if 0 <= idx < n:
                # Report the detector's box, not the Kalman-smoothed one: it is what was observed.
                box = tuple(float(v) for v in xyxy[idx])
                score = float(conf[idx])
                det_to_track[idx] = tid
            else:  # defensive: should not happen with current ultralytics
                box = tuple(float(v) for v in row[:4])
                score = float(row[5])
            t = self._tracks.get(tid)
            if t is None:
                t = Track(track_id=tid, xyxy=box, confidence=score, first_ts=ts, first_frame=self._frame)
                self._tracks[tid] = t
            t.xyxy = box
            t.confidence = score
            t.hits += 1
            t.last_ts = ts
            t.last_frame = self._frame
            t.det_index = idx
            if t.state in (TENTATIVE, OCCLUDED) and t.hits >= self.min_hits:
                was_confirmed = t.state == OCCLUDED  # came back inside the buffer: same person, no new event
                t.state = CONFIRMED
                if not was_confirmed:
                    events.append(TrackEvent("track_confirmed", tid, ts, t.snapshot()))
                else:
                    events.append(TrackEvent("track_updated", tid, ts, t.snapshot()))
            elif t.state == CONFIRMED:
                events.append(TrackEvent("track_updated", tid, ts, t.snapshot()))
            seen.add(tid)
            matched.append(t)

        alive = {s.track_id for s in self._bt.tracked_stracks} | {s.track_id for s in self._bt.lost_stracks}
        for tid in list(self._tracks):
            if tid in seen:
                continue
            t = self._tracks[tid]
            t.det_index = -1
            if tid in alive:
                if t.state == CONFIRMED:
                    t.state = OCCLUDED
                continue
            # Left ByteTrack's buffer (or an unconfirmed ByteTrack track was dropped).
            if t.hits >= self.min_hits:
                t.state = LOST
                events.append(TrackEvent("track_lost", tid, ts, t.snapshot()))
            del self._tracks[tid]

        # ByteTrack's own removed list is only needed for id bookkeeping; keep it bounded.
        if len(self._bt.removed_stracks) > 256:
            del self._bt.removed_stracks[:-256]

        return TrackerOutput(tracks=matched, events=events, det_to_track=det_to_track)

    # ── introspection ─────────────────────────────────────────────
    def tracks(self) -> List[Track]:
        return list(self._tracks.values())

    def confirmed_tracks(self) -> List[Track]:
        return [t for t in self._tracks.values() if t.state == CONFIRMED]

    def get_track(self, track_id: int) -> Optional[Track]:
        return self._tracks.get(track_id)

    def __len__(self) -> int:
        return len(self._tracks)
