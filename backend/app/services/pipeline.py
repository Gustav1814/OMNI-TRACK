"""
OmniTrack AI — Processing pipeline.

    camera thread (stream_manager) ── newest frame only ──▶ camera task (asyncio, one per camera)
                                                              │
      ┌───────────────────────────────────────────────────────┤
      │ 1. detect      ──▶ InferenceWorker thread: detect jobs from every camera are batched into
      │                    one predict([f1, f2, …]); Re-ID and specialist jobs are serialised on
      │                    the same thread, so models never compete for the CPU/GPU.
      │ 2. track       ByteTrack + tentative/confirmed/lost lifecycle (one tracker per camera).
      │ 3. Re-ID       quality gate → embed → TrackCoordinator (tracklets, Hungarian, topology).
      │ 4. analytics   zone engine on confirmed tracks only (+ fire / face hooks).
      │ 5. specialists fire every FIRE_INTERVAL_FRAMES, products every PRODUCT_INTERVAL_S,
      │                emotion every EMOTION_ANALYTICS_INTERVAL_FRAMES (only with people).
      │                Their boxes never reach the tracker or people counts.
      └ 6. render      thread pool, only while someone watches the feed or it is recording.

    dispatcher task (4 Hz) ──▶ on_results callbacks (persistence, WebSocket, plugins)
    event persister (1 Hz) ──▶ zones_repo.persist_events (zone/queue/fire/vibe/… rows)

Models are loaded once in ``start()`` and never unloaded. Under memory
pressure the per-camera FPS is lowered instead. A missing model logs an error
and disables only its feature. A failing camera only affects itself.

The public surface (add_camera, remove_camera, start, stop, get_status,
get_latest_results, get_latest_annotated_jpeg, on_results, on_lifecycle,
recording, get_analytics_snapshot, …) is kept compatible with the old
lock-step pipeline.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
from collections import Counter, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from app.ai.demographics import DemographicsClassifier, VisitAggregator, match_faces_to_tracks
from app.ai.detector import Detections, PersonDetector, SpecialistDetector, resolve_device
from app.ai.reid import PersonReID, QualityConfig, box_iou_matrix, crop_quality
from app.ai.tracker import PersonTracker, TrackerOutput
from app.analytics.engine import TrackObs, get_engine
from app.analytics import definitions as zdefs
from app.config import settings
from app.security.input_safety import redact_source, validate_camera_source
from app.services.event_bus import publish_local, subscribe_local, unsubscribe_local
from app.services.stream_manager import FrameRing, StreamConfig, StreamManager, StreamType
from app.services.track_coordinator import CoordinatorConfig, TrackCoordinator, parse_links

try:
    import psutil
except Exception:  # pragma: no cover - psutil is in requirements
    psutil = None

CALLBACK_HZ = 4.0             # on_results callbacks per second (results are merged in between)
VIEWER_TTL_S = 5.0            # render a feed only if someone fetched a frame this recently
REID_REFRESH_S = 10.0         # re-announce an ongoing (camera, global_id) presence to callbacks
OVERLAY_TTL_S = 3.0           # specialist boxes stay on the annotated frame this long
THUMB_H = 160                 # person thumbnail height (px) for Re-ID search results
THUMB_CAP = 2000              # identities with a thumbnail kept in memory (LRU, ~5 KB each)
STAGES = ("frame_age", "queue", "detect", "track", "reid", "embed", "analytics", "total", "render", "fire", "product", "emotion")
BROADCAST_EVENT_TYPES = {"alert", "crowd_level", "line_cross", "queue_wait", "queue_abandon", "fire"}
REDIS_TRACK_STREAM = "tracks.events"
REDIS_ASSIGN_STREAM = "tracks.assignments"
RING_SLACK_S = 6.0            # evidence ring keeps PRE_S + POST_S + this many seconds
RECORDER_CODECS = ("avc1", "mp4v")  # H.264 first (browser-playable); mp4v when OpenCV has no H.264 encoder
DEMOGRAPHICS_STALE_S = 600.0  # forget a track's face readings if its visit never finished


def open_video_writer(path: Path, fps: float, size: Tuple[int, int]) -> Tuple[Optional[Any], Optional[str]]:
    """First codec in RECORDER_CODECS that opens a writer; (None, None) when none does."""
    for codec in RECORDER_CODECS:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), float(fps), (int(size[0]), int(size[1])))
        if writer.isOpened():
            return writer, codec
        writer.release()
        Path(path).unlink(missing_ok=True)
    return None, None


def video_readable(path: Path) -> bool:
    cap = cv2.VideoCapture(str(path))
    try:
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        return bool(ok and frame is not None)
    finally:
        cap.release()


# ─────────────────────────────────────────────────────────────────────────────
# Public data types (kept compatible with the previous pipeline)
# ─────────────────────────────────────────────────────────────────────────────


class PipelineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class CameraResult:
    """Result of one processed frame. ``detections``/``tracks`` hold confirmed people only."""

    camera_id: int
    timestamp: float
    frame_number: int
    detections: List[Dict[str, Any]] = field(default_factory=list)
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    reid_matches: List[Dict[str, Any]] = field(default_factory=list)
    emotions: Dict[str, Any] = field(default_factory=dict)
    fire_alerts: List[Dict[str, Any]] = field(default_factory=list)
    crowd_status: Dict[str, Any] = field(default_factory=dict)
    shelf_data: Dict[str, Any] = field(default_factory=dict)
    checkout_data: Dict[str, Any] = field(default_factory=dict)
    processing_time_ms: float = 0.0
    model_status: Dict[str, Any] = field(default_factory=dict)
    # new in the rebuilt pipeline
    frame_wh: Tuple[int, int] = (0, 0)
    tentative_count: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    product_detections: List[Dict[str, Any]] = field(default_factory=list)
    fire_detections: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class GlobalState:
    total_persons_tracked: int = 0
    active_tracks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    zone_occupancy: Dict[str, int] = field(default_factory=dict)
    fire_alert_active: bool = False
    vibe_score: float = 0.0
    last_updated: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Inference worker: one thread owns every model call
# ─────────────────────────────────────────────────────────────────────────────


class _Job:
    __slots__ = ("kind", "payload", "loop", "future", "t_submit")

    def __init__(self, kind: str, payload: Any, loop: asyncio.AbstractEventLoop, future: asyncio.Future):
        self.kind = kind
        self.payload = payload
        self.loop = loop
        self.future = future
        self.t_submit = time.perf_counter()


class InferenceWorker:
    """Priority queues + one thread. Person detection (priority 0) is batched across
    cameras; Re-ID embedding (1) and specialist models (2) run one job at a time.

    Strict priority would starve fire/emotion/product whenever detection saturates the
    worker (measured on CPU: zero fire runs in 30 s with two cameras), so a lower-priority
    job that has waited longer than ``STARVE_S[prio]`` is served next. Detection keeps
    most of the worker; every queued specialist still runs within about a second.
    """

    PRIO_DETECT, PRIO_EMBED, PRIO_SPECIALIST = 0, 1, 2
    STARVE_S = {1: 0.5, 2: 1.0}

    def __init__(self, detector: PersonDetector, max_batch: int = 8):
        self.detector = detector
        self.max_batch = max(1, int(max_batch))
        self._qs: Dict[int, Deque[_Job]] = {p: deque() for p in (self.PRIO_DETECT, self.PRIO_EMBED, self.PRIO_SPECIALIST)}
        self._cv = threading.Condition()
        self.starved_served = Counter()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.batch_sizes: Deque[int] = deque(maxlen=300)
        self.busy_ms: Deque[Tuple[float, float]] = deque(maxlen=600)  # (end time, ms busy)
        self.jobs = Counter()
        self.errors = Counter()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.alive:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="inference-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=10)
        with self._cv:  # fail whatever is still queued
            left = [j for q in self._qs.values() for j in q]
            for q in self._qs.values():
                q.clear()
        for j in left:
            self._resolve(j, None, RuntimeError("inference worker stopped"))

    def depth(self) -> int:
        with self._cv:
            return sum(len(q) for q in self._qs.values())

    def _submit(self, prio: int, kind: str, payload: Any) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        if not self.alive:
            fut.set_exception(RuntimeError("inference worker not running"))
            return fut
        prio = prio if prio in self._qs else self.PRIO_SPECIALIST
        with self._cv:
            self._qs[prio].append(_Job(kind, payload, loop, fut))
            self._cv.notify()
        return fut

    async def detect(self, frame: np.ndarray) -> Tuple[Detections, float, float]:
        """``(detections, queue_wait_ms, batch_infer_ms)``"""
        return await self._submit(self.PRIO_DETECT, "detect", frame)

    async def run(self, fn: Callable, *args, prio: int = 1) -> Tuple[Any, float, float]:
        """Run ``fn(*args)`` on the worker thread: ``(result, queue_wait_ms, run_ms)``."""
        return await self._submit(prio, "call", (fn, args))

    @staticmethod
    def _resolve(job: _Job, result: Any, exc: Optional[BaseException]) -> None:
        def _set() -> None:
            if job.future.done():
                return
            if exc is not None:
                job.future.set_exception(exc)
            else:
                job.future.set_result(result)

        try:
            job.loop.call_soon_threadsafe(_set)
        except RuntimeError:  # event loop already closed
            pass

    def _next(self) -> Optional[List[_Job]]:
        """Next unit of work: a detect batch or one call. ``None`` once stopped and drained of the loop."""
        with self._cv:
            while self._running and not any(self._qs.values()):
                self._cv.wait(timeout=0.5)
            if not self._running:
                return None
            now = time.perf_counter()
            starved = [
                (now - q[0].t_submit - self.STARVE_S[p], p)
                for p, q in self._qs.items() if p in self.STARVE_S and q and now - q[0].t_submit > self.STARVE_S[p]
            ]
            if starved:
                p = max(starved)[1]
                self.starved_served[p] += 1
                return [self._qs[p].popleft()]
            for p in sorted(self._qs):
                q = self._qs[p]
                if not q:
                    continue
                if p != self.PRIO_DETECT:
                    return [q.popleft()]
                batch = []
                while q and len(batch) < self.max_batch:
                    batch.append(q.popleft())
                return batch
            return []

    def _loop(self) -> None:
        while True:
            jobs = self._next()
            if jobs is None:
                break
            if not jobs:
                continue
            if jobs[0].kind == "detect":
                self._run_detect(jobs)
            else:
                self._run_call(jobs[0])

    def _run_detect(self, batch: List[_Job]) -> None:
        t0 = time.perf_counter()
        try:
            dets = self.detector.predict_batch([j.payload for j in batch])
            exc = None
        except Exception as e:  # pragma: no cover - model specific
            dets, exc = None, e
            self.errors["detect"] += 1
            logger.warning(f"Person detection failed for a batch of {len(batch)}: {e}")
        ms = (time.perf_counter() - t0) * 1000.0
        self.batch_sizes.append(len(batch))
        self.busy_ms.append((time.time(), ms))
        self.jobs["detect"] += len(batch)
        for i, j in enumerate(batch):
            wait = (t0 - j.t_submit) * 1000.0
            self._resolve(j, None if exc else (dets[i], wait, ms), exc)

    def _run_call(self, job: _Job) -> None:
        fn, args = job.payload
        t0 = time.perf_counter()
        name = getattr(fn, "__qualname__", "call")
        try:
            res, exc = fn(*args), None
        except Exception as e:
            res, exc = None, e
            self.errors[name] += 1
        ms = (time.perf_counter() - t0) * 1000.0
        self.busy_ms.append((time.time(), ms))
        self.jobs[name] += 1
        self._resolve(job, None if exc else (res, (t0 - job.t_submit) * 1000.0, ms), exc)

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        recent = [ms for t, ms in self.busy_ms if now - t <= 10.0]
        bs = list(self.batch_sizes)
        return {
            "alive": self.alive,
            "queue_depth": self.depth(),
            "max_batch": self.max_batch,
            "avg_detect_batch": round(sum(bs) / len(bs), 2) if bs else None,
            "utilisation_10s": round(min(1.0, sum(recent) / 10_000.0), 3),
            "jobs": dict(self.jobs),
            "starved_served": {str(k): v for k, v in self.starved_served.items()},
            "errors": dict(self.errors),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Per-camera state
# ─────────────────────────────────────────────────────────────────────────────


def _pct(values, q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return round(float(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]), 1)


@dataclass
class _Cam:
    camera_id: int
    source: str
    stream_type: str
    zone: str
    fps_target: float
    enable_reid: bool
    requested_model: Optional[str]
    tracker: PersonTracker
    started_ts: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None
    epoch: int = -1
    last_seq: int = 0
    frame_number: int = 0
    frames_processed: int = 0
    detections_total: int = 0
    proc_times: Deque[float] = field(default_factory=lambda: deque(maxlen=30))
    fps_last_set: float = 0.0
    stages: Dict[str, Deque[float]] = field(default_factory=lambda: {s: deque(maxlen=120) for s in STAGES})
    errors: Deque[Tuple[float, str]] = field(default_factory=lambda: deque(maxlen=200))
    last_frame_ts: Optional[float] = None
    last_result: Optional[CameraResult] = None
    result_seq: int = 0
    dispatched_seq: int = 0
    confirmed_now: int = 0
    # pending for the next callback dispatch
    pending_fire: List[Dict[str, Any]] = field(default_factory=list)
    pending_reid: List[Dict[str, Any]] = field(default_factory=list)
    pending_events: List[Dict[str, Any]] = field(default_factory=list)
    gid_emit_ts: Dict[str, float] = field(default_factory=dict)
    # best embedded crop per live track: tid -> (quality score, JPEG); promoted to its global ID
    track_thumbs: Dict[int, Tuple[float, bytes]] = field(default_factory=dict)
    # specialists
    reid_busy: bool = False  # one embedding batch in flight per camera
    fire_busy: bool = False
    product_busy: bool = False
    emotion_busy: bool = False
    last_fire_frame: int = -(10 ** 9)
    last_product_ts: float = 0.0
    last_emotion_frame: int = -(10 ** 9)
    fire_boxes: List[Dict[str, Any]] = field(default_factory=list)
    fire_ts: float = 0.0
    fire_active: bool = False
    product_boxes: List[Dict[str, Any]] = field(default_factory=list)
    product_ts: float = 0.0
    faces: List[Dict[str, Any]] = field(default_factory=list)
    faces_ts: float = 0.0
    # rendering
    zones: List[dict] = field(default_factory=list)
    frame: Optional[np.ndarray] = None
    overlay: Optional[Dict[str, Any]] = None
    jpeg: Optional[bytes] = None
    jpeg_seq: int = 0
    render_busy: bool = False
    render_skipped: int = 0
    viewer_ts: float = 0.0
    fps_scale: float = 1.0
    # finite sources
    loop: bool = False
    finished: bool = False
    finished_at: Optional[float] = None
    # evidence pre-roll (None when evidence capture is unavailable/disabled)
    ring: Optional[FrameRing] = None

    def stage(self, name: str, ms: float) -> None:
        self.stages[name].append(float(ms))

    def error(self, where: str, exc: BaseException) -> None:
        msg = f"{where}: {type(exc).__name__}: {exc}"
        self.errors.append((time.time(), msg))
        n = self.errors_last_min()
        if n == 1 or n % 20 == 0:
            logger.warning(f"[Cam {self.camera_id}] {msg} (errors in last minute: {n})")

    def errors_last_min(self) -> int:
        cut = time.time() - 60.0
        return sum(1 for t, _ in self.errors if t >= cut)

    def processed_fps(self) -> float:
        p = self.proc_times
        if len(p) < 2 or p[-1] <= p[0]:
            return 0.0
        # stale: nothing processed for a while means 0 fps, not the last rate
        if time.time() - p[-1] > 5.0:
            return 0.0
        return round((len(p) - 1) / (p[-1] - p[0]), 2)


def _gid_color(key: Any) -> Tuple[int, int, int]:
    h = (hash(str(key)) * 2654435761) & 0xFFFFFFFF
    return (64 + (h & 0x7F), 64 + ((h >> 8) & 0x7F), 64 + ((h >> 16) & 0x7F))


def _merge_results(old: Dict[int, CameraResult], new: Dict[int, CameraResult]) -> Dict[int, CameraResult]:
    """Latest result per camera, with list fields of both kept (bounded)."""
    out = dict(old)
    for cam, r in new.items():
        prev = out.get(cam)
        if prev is None:
            out[cam] = r
            continue
        out[cam] = dataclasses.replace(
            r,
            fire_alerts=(prev.fire_alerts + r.fire_alerts)[-200:],
            reid_matches=(prev.reid_matches + r.reid_matches)[-2000:],
            events=(prev.events + r.events)[-2000:],
        )
    return out


@dataclass
class _CallbackSlot:
    fn: Callable
    task: Optional[asyncio.Task] = None
    backlog: Optional[Dict[int, CameraResult]] = None
    busy_skips: int = 0
    slow_calls: int = 0
    failures: int = 0
    last_ms: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────


class ProcessingPipeline:
    """Multi-camera detection → tracking → Re-ID → analytics pipeline."""

    def __init__(
        self,
        detector_model: Optional[str] = None,
        reid_model: Optional[str] = None,
        fire_model: Optional[str] = None,
        confidence: Optional[float] = None,
        device: Optional[str] = None,
        processing_fps: Optional[int] = None,
        max_cameras: Optional[int] = None,
        frame_buffer_size: Optional[int] = None,
        default_skip_frames: Optional[int] = None,
        reid_threshold: Optional[float] = None,
        reid_embeddings_per_id: Optional[int] = None,
        **_ignored: Any,
    ):
        # Old knobs that no longer apply: detector_model/confidence (one person model chosen by
        # device, conf 0.1 for ByteTrack), default_skip_frames (FPS is the only decimation),
        # reid_threshold/reid_embeddings_per_id (REID_T_MATCH / REID_EMBEDDINGS_PER_ID).
        self.device = resolve_device(device or settings.DEVICE)
        self.processing_fps = max(1, int(processing_fps or settings.PROCESSING_FPS))
        self.max_cameras = int(max_cameras or settings.MAX_CAMERAS)
        self.frame_buffer_size = int(frame_buffer_size or settings.FRAME_BUFFER_SIZE)
        self.default_skip_frames = 0
        self._detector_model, self._person_imgsz = settings.person_model_for(self.device)
        self._reid_arch = reid_model or settings.REID_MODEL
        self._fire_model_path = fire_model or settings.FIRE_MODEL_PATH

        self.state = PipelineState.IDLE
        self.stream_manager = StreamManager()
        self.global_state = GlobalState()

        # models (loaded once in start(), never unloaded)
        self.detector: Optional[PersonDetector] = None
        self.reid: Optional[PersonReID] = None
        self.fire_model: Optional[SpecialistDetector] = None
        self.product_model: Optional[SpecialistDetector] = None
        self.emotion = None
        self.demographics: Optional[DemographicsClassifier] = None
        self._demo_visits = VisitAggregator()
        self._demo_rows: Deque[Dict[str, Any]] = deque(maxlen=10_000)
        self._demo_written = 0
        self._demo_skipped_cams: set = set()
        self._evidence_capture = self._load_evidence()
        self._ring_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ring")
        self._model_errors: Dict[str, str] = {}
        self._models_loaded = False
        self._load_lock: Optional[asyncio.Lock] = None
        self._worker: Optional[InferenceWorker] = None
        self._render_pool = ThreadPoolExecutor(
            max_workers=max(1, int(settings.PIPELINE_RENDER_THREADS)), thread_name_prefix="render"
        )

        self.coordinator = TrackCoordinator(self._coordinator_config())
        self._quality = QualityConfig.from_settings()
        self._load_topology_from_settings()
        self.engine = get_engine()

        self._cams: Dict[int, _Cam] = {}
        self._callbacks: List[_CallbackSlot] = []
        self._lifecycle_hooks: List[Callable] = []
        self._tasks: set = set()
        self._bg_tasks: List[asyncio.Task] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._broadcast = None
        self._bus = None
        self._bus_queue: Deque[Tuple[str, str, Dict[str, Any]]] = deque(maxlen=5000)

        self._event_buffer: Deque[Dict[str, Any]] = deque(maxlen=50_000)
        self._events_dropped = 0
        self._events_persisted = 0
        self._fire_history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self._gid_last_cam: "OrderedDict[str, int]" = OrderedDict()
        # gid -> (score, JPEG, camera, ts): best-quality crop per identity, memory only (never persisted)
        self._gid_thumbs: "OrderedDict[str, Tuple[float, bytes, int, float]]" = OrderedDict()
        self._reid_gate = Counter()
        self._total_detections = 0
        self._mem = {"pct": None, "scale": 1.0, "pressure": False}

        self._recorders: Dict[int, Tuple[Any, Path, threading.Lock]] = {}
        self._detection_logs: Dict[int, Dict[str, Any]] = {}
        self._logs_dir = Path(settings.FOOTAGE_DIR).parent / "logs"

        # compat attributes read by other modules
        self._camera_zones: Dict[int, str] = {}
        self._camera_models: Dict[int, Tuple[str, ...]] = {}
        self._detectors: Dict[str, Any] = {}
        self.crowd = None
        self.vibe_engine = None
        self._zones_handler = self._on_zones_changed
        subscribe_local("zones_changed", self._zones_handler)

    # ── evidence ────────────────────────────────────────────────────
    @staticmethod
    def _load_evidence() -> Optional[Callable]:
        if not settings.EVIDENCE_ENABLED:
            logger.info("Alert evidence disabled (EVIDENCE_ENABLED=false); no frame ring kept")
            return None
        try:
            from app.services.evidence import schedule_capture
        except Exception as e:
            logger.error(f"Alert evidence unavailable ({e}); alerts are recorded without snapshots/clips")
            return None
        return schedule_capture

    def _ring_bytes_per_cam(self) -> int:
        total = max(1, int(settings.EVIDENCE_RING_TOTAL_MB)) * 1024 * 1024
        return total // max(1, len(self._cams) or 1)

    def _rebalance_rings(self) -> None:
        cap = self._ring_bytes_per_cam()
        for cs in self._cams.values():
            if cs.ring is not None:
                cs.ring.set_max_bytes(cap)

    def _new_ring(self) -> Optional[FrameRing]:
        if self._evidence_capture is None:
            return None
        return FrameRing(
            seconds=float(settings.EVIDENCE_PRE_S) + float(settings.EVIDENCE_POST_S) + RING_SLACK_S,
            max_bytes=self._ring_bytes_per_cam(),
            fps=float(settings.EVIDENCE_RING_FPS),
            max_width=int(settings.EVIDENCE_RING_MAX_WIDTH),
        )

    def _capture_evidence(self, ev: Dict[str, Any], cs: _Cam) -> None:
        if self._evidence_capture is None or cs.ring is None:
            return
        try:
            self._evidence_capture(ev, str(cs.camera_id), cs.ring.frames)
        except Exception as e:  # never let evidence break event handling
            logger.error(f"[Cam {cs.camera_id}] evidence capture not scheduled: {e}")

    # ── configuration ───────────────────────────────────────────────
    @staticmethod
    def _coordinator_config() -> CoordinatorConfig:
        t_match = float(settings.REID_T_MATCH)
        return CoordinatorConfig(
            t_match=t_match,
            t_update=max(float(settings.REID_T_UPDATE), t_match),
            top_k=int(settings.REID_TRACKLET_TOP_K),
            min_crops=int(settings.REID_TRACKLET_MIN_CROPS),
            max_wait_s=float(settings.REID_TRACKLET_MAX_WAIT_S),
            max_views=int(settings.REID_EMBEDDINGS_PER_ID),
            max_identities=int(settings.REID_GALLERY_MAX_IDENTITIES),
            default_max_transit_s=float(settings.REID_DEFAULT_MAX_TRANSIT_S),
            reentry_max_s=float(settings.REID_REENTRY_MAX_S),
            fusion_distance_m=float(settings.REID_FUSION_DISTANCE_M),
            handoff_bonus=float(settings.REID_HANDOFF_BONUS),
        )

    def _load_topology_from_settings(self) -> None:
        raw = (settings.REID_CAMERA_TOPOLOGY or "").strip()
        if not raw:
            return
        try:
            self.coordinator.set_topology(parse_links(json.loads(raw)))
        except Exception as e:
            logger.error(f"REID_CAMERA_TOPOLOGY is invalid, using default gating: {e}")

    async def load_reid_config_from_db(self) -> Dict[str, int]:
        """Topology links and floor homographies saved via /api/reid (DB wins over the env var)."""
        try:
            from app.database import AsyncSessionLocal
            from app.services.crud import ReIDConfigService

            async with AsyncSessionLocal() as db:
                links = await ReIDConfigService.list_links(db)
                calib = await ReIDConfigService.list_calibrations(db)
        except Exception as e:
            logger.warning(f"Re-ID topology/calibration not loaded from DB: {e}")
            return {"links": 0, "calibrated": 0}
        if links:
            self.coordinator.set_topology(parse_links(links))
        n = 0
        for row in calib:
            try:
                self.coordinator.set_calibration(int(row["camera_id"]), row["homography"])
                n += 1
            except Exception as e:
                logger.warning(f"Ignoring calibration of camera {row.get('camera_id')}: {e}")
        logger.info(f"Re-ID config from DB: {len(links)} topology link(s), {n} calibrated camera(s)")
        return {"links": len(links), "calibrated": n}

    def attach_broadcast(self, broadcast) -> None:
        """WebSocket BroadcastService used for zone alerts and cross-camera Re-ID pushes."""
        self._broadcast = broadcast

    # ── callbacks ───────────────────────────────────────────────────
    def on_results(self, callback: Callable) -> None:
        self._callbacks.append(_CallbackSlot(callback))

    def on_lifecycle(self, hook: Callable) -> None:
        self._lifecycle_hooks.append(hook)

    async def _emit_lifecycle(self, event: str, payload: Dict[str, Any]) -> None:
        for hook in self._lifecycle_hooks:
            try:
                res = hook(event, payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug(f"Lifecycle hook failed for {event}: {e}")

    def _spawn(self, coro) -> Optional[asyncio.Task]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return None
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ── models ──────────────────────────────────────────────────────
    def _load_models_sync(self) -> None:
        try:
            import torch

            torch.set_num_threads(max(1, int(getattr(settings, "TORCH_THREADS", 0) or torch.get_num_threads())))
        except Exception:
            pass
        try:
            self.detector = PersonDetector(
                self._detector_model,
                conf=settings.PERSON_CONF,
                iou=settings.PERSON_IOU,
                imgsz=self._person_imgsz,
                device=self.device,
                export=settings.DETECTOR_EXPORT,
            )
            self._detectors = {self.detector.name: self.detector}
            logger.info(f"Person detector: {self.detector.info()}")
        except Exception as e:
            self._model_errors["person"] = str(e)
            logger.error(f"Person detector failed to load ({self._detector_model}): {e}")
        self.reid = PersonReID(self._reid_arch, weights=settings.REID_WEIGHTS_PATH, device=self.device)
        if not self.reid.is_loaded:
            self._model_errors["reid"] = self.reid.error or "not loaded"
        for attr, path, conf, label in (
            ("fire_model", self._fire_model_path, 0.25, "fire"),
            ("product_model", settings.PRODUCT_MODEL_PATH if settings.PRODUCT_INTERVAL_S > 0 else "", 0.4, "product"),
        ):
            if not path:
                continue
            try:
                setattr(self, attr, SpecialistDetector(path, conf=conf, device=self.device, label=label))
                logger.info(f"{label} model loaded: {Path(path).name}")
            except Exception as e:
                self._model_errors[label] = str(e)
                logger.error(f"{label} model disabled ({path}): {e}")
        try:
            from app.ai.emotion import EmotionRecognizer

            em = EmotionRecognizer()
            if em.available:
                self.emotion = em
            else:
                self._model_errors["emotion"] = "face/emotion weights not available"
                logger.error("Emotion analytics disabled: face/emotion weights not available")
        except Exception as e:
            self._model_errors["emotion"] = str(e)
            logger.error(f"Emotion analytics disabled: {e}")
        if self.emotion is None:
            self._model_errors["demographics"] = "needs the face detector of the emotion module"
        else:
            demo = DemographicsClassifier()
            if demo.available:
                self.demographics = demo
            else:
                self._model_errors["demographics"] = "model_not_installed"

    async def _ensure_models(self) -> bool:
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        async with self._load_lock:
            if not self._models_loaded:
                t0 = time.perf_counter()
                await asyncio.to_thread(self._load_models_sync)
                self._models_loaded = True
                logger.info(f"Models loaded in {time.perf_counter() - t0:.1f}s on {self.device}")
            if self.detector is not None and (self._worker is None or not self._worker.alive):
                self._worker = InferenceWorker(self.detector, max_batch=max(1, self.max_cameras))
                self._worker.start()
        return self.detector is not None

    @property
    def _reid_ready(self) -> bool:
        return self.reid is not None and self.reid.is_loaded

    # ── cameras ─────────────────────────────────────────────────────
    def _new_tracker(self, fps: float) -> PersonTracker:
        return PersonTracker(
            fps=fps,
            track_high_thresh=settings.TRACK_HIGH_THRESH,
            track_low_thresh=settings.TRACK_LOW_THRESH,
            new_track_thresh=settings.TRACK_NEW_THRESH,
            match_thresh=settings.TRACK_MATCH_THRESH,
            buffer_s=settings.TRACK_BUFFER_S,
            min_hits=settings.TRACK_MIN_HITS,
        )

    def add_camera(
        self,
        camera_id: int,
        source: str,
        stream_type: str = "rtsp",
        zone: str = "default",
        fps: int = settings.PROCESSING_FPS,
        skip_frames: int = 0,
        roi: Optional[Dict] = None,
        model_path: Any = None,
        enable_reid: Optional[bool] = True,
        tracker_mode: str = "bytetrack",
        loop: bool = False,
    ) -> None:
        """Register (or replace) a camera. ``source`` is an RTSP/HTTP URL, a file path or a webcam index.

        ``loop`` (files only): replay from the start at end of file as a new timeline. Off (default):
        the camera stops at end of file and reports ``finished`` in status.

        ``skip_frames``, ``model_path`` and ``tracker_mode`` are accepted for API compatibility:
        frames are decimated by FPS only, every camera uses the one person detector, and
        tracking is always ByteTrack.
        """
        camera_id = int(camera_id)
        # SSRF / path-traversal guard for every caller (UnsafeInputError is a ValueError -> 400);
        # done before an existing feed is torn down so a bad replacement leaves it running.
        source = validate_camera_source(str(source))
        replacing = camera_id in self._cams or self.stream_manager.has_camera(camera_id)
        if not replacing and self.stream_manager.total_count >= self.max_cameras:
            raise ValueError(
                f"Maximum camera limit reached ({self.max_cameras}). "
                "Increase MAX_CAMERAS or remove an inactive camera."
            )
        if replacing:
            self._teardown_camera(camera_id, emit=False)
        fps_target = float(max(1, min(int(fps or self.processing_fps), self.processing_fps)))
        config = StreamConfig(
            camera_id=camera_id,
            source=source,
            stream_type=StreamType(stream_type),
            fps_target=fps_target * self._mem["scale"],
            buffer_size=self.frame_buffer_size,
            skip_frames=0,
            roi=roi,
            loop=bool(loop),
        )
        self.stream_manager.add_camera(config)
        requested = None
        if model_path:
            requested = model_path if isinstance(model_path, str) else ",".join(str(m) for m in model_path)
            if Path(str(requested)).name != Path(self._detector_model).name:
                logger.info(
                    f"Camera {camera_id}: per-feed model {requested!r} ignored; "
                    f"the person stream uses {Path(self._detector_model).name}"
                )
        cs = _Cam(
            camera_id=camera_id,
            source=str(source),
            stream_type=str(stream_type),
            zone=zone or "default",
            fps_target=fps_target,
            enable_reid=bool(enable_reid if enable_reid is not None else True),
            requested_model=requested,
            tracker=self._new_tracker(fps_target),
            loop=bool(loop),
        )
        cs.fps_scale = self._mem["scale"]
        self._cams[camera_id] = cs
        cs.ring = self._new_ring()
        self._rebalance_rings()
        self._camera_zones[camera_id] = cs.zone
        self._camera_models[camera_id] = (self._detector_model,)
        logger.info(
            f"Camera {camera_id} added: {stream_type} zone={cs.zone} fps={fps_target:g} "
            f"reid={cs.enable_reid} loop={cs.loop} detector={Path(self._detector_model).name}"
        )
        if self.state == PipelineState.RUNNING:
            self.stream_manager.start_camera(camera_id)
            self._start_camera_task(cs)
        self._spawn(self._emit_lifecycle("camera_added", {
            "camera_id": camera_id, "source": redact_source(source), "stream_type": stream_type, "zone": cs.zone,
        }))

    def _start_camera_task(self, cs: _Cam) -> None:
        if cs.task is None or cs.task.done():
            cs.finished, cs.finished_at = False, None
            cs.task = self._spawn(self._camera_loop(cs))
            self._spawn(self._load_zones(cs.camera_id))

    def _teardown_camera(self, camera_id: int, emit: bool = True) -> Optional[str]:
        self._stop_recording_impl(camera_id)
        cs = self._cams.pop(camera_id, None)
        self.stream_manager.remove_camera(camera_id)
        zone = self._camera_zones.pop(camera_id, None)
        self._camera_models.pop(camera_id, None)
        if cs is None:
            return zone
        if cs.task is not None:
            cs.task.cancel()
        now = time.time()
        try:
            lost = cs.tracker.reset()
            ev = []
            for e in lost:
                ev += self.engine.on_track_lost(str(camera_id), now, e.track_id)
            ev += self.engine.on_camera_stopped(str(camera_id), now)
            self._queue_events(ev, cs)
            self._handle_reid_events(self.coordinator.drop_camera(camera_id, now))
        except Exception as e:
            logger.warning(f"Camera {camera_id} teardown: {e}")
        self._demo_visits.drop_camera(str(camera_id))
        self._rebalance_rings()
        return zone

    def remove_camera(self, camera_id: int) -> None:
        camera_id = int(camera_id)
        zone = self._teardown_camera(camera_id)
        logger.info(f"Camera {camera_id} removed")
        self._spawn(self._emit_lifecycle("camera_removed", {"camera_id": camera_id, "zone": zone}))

    # ── zones ───────────────────────────────────────────────────────
    def _on_zones_changed(self, payload: Dict[str, Any]) -> None:
        """event_bus "zones_changed" {camera_id}; may be called from any thread."""
        try:
            cam = int(payload.get("camera_id"))
        except (TypeError, ValueError):
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(lambda: self._spawn(self._load_zones(cam)))

    async def _load_zones(self, camera_id: int) -> None:
        zones = None
        try:
            from app.analytics import zones_repo

            zones = await zones_repo.load_zones(str(camera_id))
            self.engine.set_zones(str(camera_id), zones)
        except Exception as e:
            logger.warning(f"[Cam {camera_id}] zones not loaded from DB: {e}")
        cs = self._cams.get(camera_id)
        if cs is not None:
            cs.zones = self.engine.get_zones(str(camera_id))
            if zones is not None:
                logger.info(f"[Cam {camera_id}] {len(cs.zones)} zone(s) active")

    # ── lifecycle ───────────────────────────────────────────────────
    async def start(self) -> None:
        if self.state in (PipelineState.RUNNING, PipelineState.STARTING):
            return
        if self.stream_manager.total_count <= 0:
            self.state = PipelineState.IDLE
            logger.warning("Pipeline start skipped: no cameras registered")
            return
        self.state = PipelineState.STARTING
        self._loop = asyncio.get_running_loop()
        try:
            ok = await self._ensure_models()
        except Exception as e:
            ok = False
            self._model_errors["person"] = str(e)
            logger.error(f"Model loading failed: {e}")
        if not ok:
            self.state = PipelineState.ERROR
            logger.error("Pipeline not started: no person detector (see model errors on /api/pipeline/health)")
            return
        self._init_bus()
        self.stream_manager.start_all()
        self.state = PipelineState.RUNNING
        for cs in self._cams.values():
            self._start_camera_task(cs)
        self._bg_tasks = [
            asyncio.create_task(self._dispatch_loop(), name="pipeline-dispatch"),
            asyncio.create_task(self._persist_loop(), name="pipeline-persist"),
            asyncio.create_task(self._housekeeping_loop(), name="pipeline-housekeeping"),
        ]
        if self._bus is not None:
            self._bg_tasks.append(asyncio.create_task(self._bus_loop(), name="pipeline-bus"))
        await self._emit_lifecycle("pipeline_started", {
            "cameras": self.stream_manager.total_count, "target_fps": self.processing_fps,
        })
        logger.info(
            f"Pipeline running: {len(self._cams)} camera(s), target {self.processing_fps} fps, "
            f"detector {self.detector.name} ({self.detector.backend}) on {self.device}"
        )

    async def stop(self) -> None:
        if self.state not in (PipelineState.STARTING, PipelineState.RUNNING, PipelineState.ERROR):
            return
        self.state = PipelineState.STOPPING
        for cs in self._cams.values():
            if cs.task is not None:
                cs.task.cancel()
        tasks = [cs.task for cs in self._cams.values() if cs.task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)
        for cs in self._cams.values():
            cs.task = None
        for t in self._bg_tasks:
            t.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks = []
        for cid in list(self._recorders):
            self._stop_recording_impl(cid)
        self.stream_manager.stop_all()
        # close open dwells/visits so they are not lost, then flush events
        now = time.time()
        for cid in list(self._cams):
            try:
                self._queue_events(self.engine.on_camera_stopped(str(cid), now))
                self._handle_reid_events(self.coordinator.drop_camera(cid, now))
            except Exception as e:
                logger.debug(f"stop: camera {cid} flush failed: {e}")
        await self._flush_events()
        if self._worker is not None:
            await asyncio.to_thread(self._worker.stop)
            self._worker = None
        if self._bus is not None:
            try:
                await self._bus.close()
            except Exception:
                pass
            self._bus = None
        self.state = PipelineState.IDLE
        await self._emit_lifecycle("pipeline_stopped", {
            "total_persons_tracked": self.global_state.total_persons_tracked,
            "total_detections": self._total_detections,
        })
        logger.info("Pipeline stopped")

    def shutdown(self) -> None:
        unsubscribe_local("zones_changed", self._zones_handler)
        self._render_pool.shutdown(wait=False, cancel_futures=True)
        self._ring_pool.shutdown(wait=False, cancel_futures=True)

    # ── per-camera processing ───────────────────────────────────────
    async def _camera_loop(self, cs: _Cam) -> None:
        cam = cs.camera_id
        try:
            while self.state == PipelineState.RUNNING and self._cams.get(cam) is cs:
                stream = self.stream_manager._streams.get(cam)
                pkt = stream.get_latest(cs.last_seq) if stream is not None else None
                if pkt is None:
                    if stream is not None and stream.finished:
                        self._finish_camera(cs)
                        return
                    await asyncio.sleep(0.01)
                    continue
                cs.last_seq = pkt.seq
                try:
                    await self._process_frame(cs, pkt)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    cs.error("process", e)
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    def _finish_camera(self, cs: _Cam) -> None:
        """File source reached its end with loop off: close tracks/visits, keep the camera listed as finished."""
        cam = cs.camera_id
        now = time.time()
        ts = cs.last_frame_ts if cs.last_frame_ts is not None else now
        try:
            events: List[Dict[str, Any]] = []
            reid_events: List[Dict[str, Any]] = []
            for e in cs.tracker.reset():
                events += self.engine.on_track_lost(str(cam), ts, e.track_id)
                if self._reid_ready and cs.enable_reid:
                    reid_events += self.coordinator.on_track_lost(cam, e.track_id, ts)
                self._publish_track_event(cam, e, None)
            events += self.engine.on_camera_stopped(str(cam), ts)
            self._queue_events(events)
            self._handle_reid_events(reid_events + self.coordinator.drop_camera(cam, now))
        except Exception as e:
            cs.error("finish", e)
        self._stop_recording_impl(cam)
        cs.epoch = -1  # a restart is a new timeline
        cs.confirmed_now = 0
        cs.finished, cs.finished_at = True, now
        logger.info(f"[Cam {cam}] source finished after {cs.frames_processed} processed frames (loop off)")
        self._spawn(self._emit_lifecycle("camera_finished", {
            "camera_id": cam, "zone": cs.zone, "frames_processed": cs.frames_processed,
        }))

    async def _process_frame(self, cs: _Cam, pkt) -> None:
        t_start = time.perf_counter()
        cam = cs.camera_id
        frame, ts = pkt.frame, float(pkt.ts)
        h, w = frame.shape[:2]
        cs.frame_number += 1
        cs.last_frame_ts = ts
        cs.stage("frame_age", (time.time() - ts) * 1000.0)
        ring = cs.ring
        if ring is not None and ring.due(ts):
            ring.busy = True  # at most one queued encode per camera; never blocks this loop
            try:
                self._ring_pool.submit(ring.push, frame, ts)
            except RuntimeError:  # pool shut down
                ring.busy = False
        events: List[Dict[str, Any]] = []
        reid_events: List[Dict[str, Any]] = []

        if pkt.epoch != cs.epoch:  # reconnect or file loop: the old tracks are meaningless
            if cs.epoch != -1:
                out_lost = cs.tracker.reset()
                for e in out_lost:
                    events += self.engine.on_track_lost(str(cam), ts, e.track_id)
                    if self._reid_ready and cs.enable_reid:
                        reid_events += self.coordinator.on_track_lost(cam, e.track_id, ts)
                    self._publish_track_event(cam, e, None)
            cs.epoch = pkt.epoch

        # 1. detect (batched across cameras on the inference worker)
        dets, wait_ms, infer_ms = await self._worker.detect(frame)
        cs.stage("queue", wait_ms)
        cs.stage("detect", infer_ms)
        if self._cams.get(cam) is not cs:
            return

        # 2. track
        t = time.perf_counter()
        out: TrackerOutput = cs.tracker.update(dets.xyxy, dets.conf, ts)
        cs.proc_times.append(time.time())
        if time.time() - cs.fps_last_set >= 2.0 and len(cs.proc_times) >= 5:
            measured = cs.processed_fps()
            if measured > 0:
                cs.tracker.set_fps(measured)
            cs.fps_last_set = time.time()
        confirmed = out.confirmed
        lost = [e for e in out.events if e.type == "track_lost"]
        cs.stage("track", (time.perf_counter() - t) * 1000.0)

        # 3. Re-ID (lost tracklets are finalised before new ones are matched)
        t = time.perf_counter()
        reid_on = self._reid_ready and cs.enable_reid
        for e in lost:
            if reid_on:
                reid_events += self.coordinator.on_track_lost(cam, e.track_id, ts)
        if reid_on:
            reid_events += await self._reid_step(cs, frame, (w, h), dets, out, ts)
        if self._cams.get(cam) is not cs:
            return
        gids = {tr.track_id: self.coordinator.global_id(cam, tr.track_id) for tr in confirmed} if reid_on else {}
        cs.stage("reid", (time.perf_counter() - t) * 1000.0)

        # 4. analytics on confirmed tracks
        t = time.perf_counter()
        for e in lost:
            events += self.engine.on_track_lost(str(cam), ts, e.track_id)
        obs = [
            TrackObs(tr.track_id, gids.get(tr.track_id), tuple(float(v) for v in tr.xyxy), float(tr.confidence))
            for tr in confirmed
        ]
        events += self.engine.on_tracks(str(cam), ts, (w, h), obs)
        for e in out.events:
            if e.type != "track_updated" or e.track.get("state") == "confirmed":
                self._publish_track_event(cam, e, gids.get(e.track_id))
        self._handle_reid_events(reid_events)
        self._queue_events(events)
        cs.stage("analytics", (time.perf_counter() - t) * 1000.0)

        # 5. specialists on their own cadence (never block this frame)
        self._schedule_specialists(cs, frame, ts, has_people=bool(confirmed),
                                   track_boxes={int(tr.track_id): tuple(float(v) for v in tr.xyxy) for tr in confirmed})

        # 6. result
        tracks_out = []
        for tr in confirmed:
            gid = gids.get(tr.track_id)
            info = self.coordinator.tracklet_info(cam, tr.track_id) if reid_on else None
            tracks_out.append({
                "track_id": int(tr.track_id),
                "global_id": gid,
                "bbox": [round(v, 1) for v in tr.bbox_xywh],
                "confidence": round(float(tr.confidence), 3),
                "class_name": "person",
                "zone": cs.zone,
                "state": tr.state,
                "hits": int(tr.hits),
                "first_seen": tr.first_ts,
                "similarity": info.get("similarity") if info else None,
                "reid_reason": info.get("reason") if info else None,
            })
            if gid and ts - cs.gid_emit_ts.get(gid, 0.0) >= REID_REFRESH_S:
                cs.gid_emit_ts[gid] = ts
                cs.pending_reid.append({
                    "global_id": gid, "camera_id": cam, "track_id": int(tr.track_id),
                    "bbox": [round(v, 1) for v in tr.bbox_xywh], "timestamp": ts, "source": "refresh",
                    "similarity": info.get("similarity") if info else None,
                })
        cs.frames_processed += 1
        cs.detections_total += len(tracks_out)
        cs.confirmed_now = len(tracks_out)
        self._total_detections += len(tracks_out)
        frame_alerts = [ev for ev in events if ev.get("type") == "fire"]
        result = CameraResult(
            camera_id=cam,
            timestamp=ts,
            frame_number=cs.frame_number,
            detections=tracks_out,
            tracks=tracks_out,
            reid_matches=[m for m in cs.pending_reid if m.get("timestamp") == ts],
            emotions=self._emotion_view(cs, ts),
            fire_alerts=[self._fire_alert(cs, ev) for ev in frame_alerts if self._ev_field(ev, "action") in ("start", "reopen")],
            crowd_status={"zone": cs.zone, "person_count": len(tracks_out), "camera_id": cam},
            processing_time_ms=round((time.perf_counter() - t_start) * 1000.0, 1),
            model_status=self._model_status(cs),
            frame_wh=(int(w), int(h)),
            tentative_count=len(out.tracks) - len(confirmed),
            events=events,
            product_detections=list(cs.product_boxes) if ts - cs.product_ts <= max(OVERLAY_TTL_S, settings.PRODUCT_INTERVAL_S * 2) else [],
            fire_detections=list(cs.fire_boxes) if ts - cs.fire_ts <= OVERLAY_TTL_S else [],
        )
        cs.last_result = result
        cs.result_seq += 1
        if cam in self._detection_logs:
            self._log_detections(cs, result)

        # 7. render only for viewers / recording
        cs.frame = frame
        cs.overlay = self._overlay(cs, confirmed, gids, ts)
        cs.stage("total", (time.perf_counter() - t_start) * 1000.0)
        self._maybe_render(cs)

    async def _reid_step(self, cs: _Cam, frame: np.ndarray, wh: Tuple[int, int], dets: Detections,
                         out: TrackerOutput, ts: float) -> List[Dict[str, Any]]:
        cam = cs.camera_id
        confirmed = out.confirmed
        if not confirmed:
            return self.coordinator.step(ts)
        boxes = np.array([tr.xyxy for tr in confirmed], np.float32).reshape(-1, 4)
        others = dets.xyxy.reshape(-1, 4).astype(np.float32)
        iou = box_iou_matrix(boxes, others) if len(others) else np.zeros((len(boxes), 0), np.float32)
        if iou.size:
            iou[:, dets.conf < 0.25] = 0.0  # ignore boxes too weak to be people
        cands = []
        for i, tr in enumerate(confirmed):
            if cs.reid_busy:  # previous batch still computing: no new crops this frame
                break
            if not self.coordinator.wants_embedding(cam, tr.track_id, ts):
                continue
            row = iou[i].copy() if iou.size else np.zeros(0)
            if 0 <= tr.det_index < row.shape[0]:
                row[tr.det_index] = 0.0
            ok, reason, score = crop_quality(tr.xyxy, tr.confidence, wh, float(row.max()) if row.size else 0.0, self._quality)
            self._reid_gate[reason] += 1
            if ok:
                cands.append((score, tr))
        self.coordinator.observe(
            cam, ts, [(tr.track_id, tr.bbox_xywh, float(tr.confidence), tr.foot_point) for tr in confirmed], {}
        )
        if cands:
            # Embedding (~350 ms/batch on CPU) runs off the frame loop: the camera keeps detecting and
            # tracking while it computes, and the crops join their tracklets when it finishes.
            cands.sort(key=lambda c: -c[0])
            cands = cands[: max(1, int(settings.MAX_REID_DETECTIONS_PER_FRAME))]
            H, W = frame.shape[:2]
            crops = []
            for _s, tr in cands:
                x1, y1, x2, y2 = (int(round(v)) for v in tr.xyxy)
                crops.append(frame[max(0, y1):min(H, y2), max(0, x1):min(W, x2)].copy())
            cs.reid_busy = True
            self._spawn(self._run_embed(cs, ts, [(float(s), tr.track_id) for s, tr in cands], crops))
        return self.coordinator.step(ts)

    async def _run_embed(self, cs: _Cam, ts: float, cands: List[Tuple[float, int]], crops: List[np.ndarray]) -> None:
        try:
            embs, _w, ms = await self._worker.run(self.reid.extract_batch, crops, prio=InferenceWorker.PRIO_EMBED)
            cs.stage("embed", ms)
            if self._cams.get(cs.camera_id) is not cs or embs is None:
                return
            # tracks that ended while the batch ran were already finalised; their crops are dropped
            self._note_thumbs(cs, ts, cands, crops)
            self.coordinator.add_embeddings(cs.camera_id, ts, {tid: (s, e) for (s, tid), e in zip(cands, embs)})
            self._handle_reid_events(self.coordinator.step(max(ts, cs.last_frame_ts)))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            cs.error("reid", e)
        finally:
            cs.reid_busy = False

    # ── person thumbnails (for "find this person") ──────────────────
    def _note_thumbs(self, cs: _Cam, ts: float, cands: List[Tuple[float, int]], crops: List[np.ndarray]) -> None:
        """Keep the highest-quality crop seen for each track (only crops that passed the Re-ID quality
        gate reach here); promote it to the track's global ID when it has one."""
        for (score, tid), crop in zip(cands, crops):
            old = cs.track_thumbs.get(tid)
            if (old is not None and score <= old[0]) or crop.size == 0:
                continue
            h, w = crop.shape[:2]
            scale = THUMB_H / float(h)
            small = cv2.resize(crop, (max(1, int(round(w * scale))), THUMB_H), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if not ok:
                continue
            cs.track_thumbs[tid] = (float(score), buf.tobytes())
            gid = self.coordinator.global_id(cs.camera_id, tid)
            if gid:
                self._promote_thumb(gid, cs, tid, ts)
        while len(cs.track_thumbs) > 512:
            cs.track_thumbs.pop(next(iter(cs.track_thumbs)))

    def _promote_thumb(self, gid: str, cs: _Cam, tid: int, ts: float) -> None:
        t = cs.track_thumbs.get(tid)
        if t is None:
            return
        cur = self._gid_thumbs.get(gid)
        if cur is None or t[0] > cur[0]:
            self._gid_thumbs[gid] = (t[0], t[1], cs.camera_id, ts)
        self._gid_thumbs.move_to_end(gid)
        while len(self._gid_thumbs) > THUMB_CAP:
            self._gid_thumbs.popitem(last=False)

    def person_thumb(self, gid: str) -> Optional[Tuple[bytes, int, float]]:
        """Best crop for a global ID: (JPEG, camera_id, ts), or None when none is in memory."""
        t = self._gid_thumbs.get(str(gid))
        return (t[1], t[2], t[3]) if t is not None else None

    # ── specialists ─────────────────────────────────────────────────
    def _schedule_specialists(self, cs: _Cam, frame: np.ndarray, ts: float, has_people: bool,
                              track_boxes: Optional[Dict[int, Tuple[float, ...]]] = None) -> None:
        if self.fire_model is not None and not cs.fire_busy and \
                cs.frame_number - cs.last_fire_frame >= max(1, int(settings.FIRE_INTERVAL_FRAMES)):
            cs.fire_busy, cs.last_fire_frame = True, cs.frame_number
            self._spawn(self._run_fire(cs, frame, ts))
        if self.product_model is not None and not cs.product_busy and settings.PRODUCT_INTERVAL_S > 0 and \
                ts - cs.last_product_ts >= settings.PRODUCT_INTERVAL_S:
            cs.product_busy, cs.last_product_ts = True, ts
            self._spawn(self._run_product(cs, frame, ts))
        if self.emotion is not None and has_people and not cs.emotion_busy and \
                cs.frame_number - cs.last_emotion_frame >= max(1, int(settings.EMOTION_ANALYTICS_INTERVAL_FRAMES)):
            cs.emotion_busy, cs.last_emotion_frame = True, cs.frame_number
            self._spawn(self._run_emotion(cs, frame, ts, track_boxes or {}))

    async def _run_fire(self, cs: _Cam, frame: np.ndarray, ts: float) -> None:
        try:
            res, _w, ms = await self._worker.run(self.fire_model.predict_batch, [frame], prio=InferenceWorker.PRIO_SPECIALIST)
            cs.stage("fire", ms)
            if self._cams.get(cs.camera_id) is not cs:
                return
            boxes = res[0] if res else []
            # Overlay only what can count towards an alert: sub-threshold boxes (red signage,
            # warm lighting) would read as fire to an operator although no alert can follow.
            fh, fw = frame.shape[:2]
            cs.fire_boxes = [b for b in boxes if zdefs.fire_qualifies(b["xyxy"], b["confidence"], (fw, fh))]
            cs.fire_ts = ts
            events = self.engine.on_fire(
                str(cs.camera_id), ts,
                [(tuple(float(v) for v in b["xyxy"]), float(b["confidence"]), str(b["class_name"])) for b in boxes],
            )
            self._queue_events(events)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            cs.error("fire", e)
        finally:
            cs.fire_busy = False

    async def _run_product(self, cs: _Cam, frame: np.ndarray, ts: float) -> None:
        try:
            res, _w, ms = await self._worker.run(self.product_model.predict_batch, [frame], prio=InferenceWorker.PRIO_SPECIALIST)
            cs.stage("product", ms)
            cs.product_boxes, cs.product_ts = (res[0] if res else []), ts
        except asyncio.CancelledError:
            pass
        except Exception as e:
            cs.error("product", e)
        finally:
            cs.product_busy = False

    def _faces_job(self, frame: np.ndarray, cam: int, zone: str,
                   track_boxes: Dict[int, Tuple[float, ...]]) -> Tuple[List[Dict[str, Any]], List[Tuple[int, Any, Any]]]:
        """Emotion faces, then age/gender for faces that belong to exactly one confirmed track whose
        visit still wants readings. Runs on the inference worker thread."""
        faces = self.emotion.analyze_frame(frame, cam, zone) or []
        readings: List[Tuple[int, Any, Any]] = []
        if self.demographics is None or not faces or not track_boxes:
            return faces, readings
        key = str(cam)
        owner = match_faces_to_tracks([f["bbox"] for f in faces], track_boxes)
        picks = [(i, tid) for i, tid in owner.items() if self._demo_visits.wants(key, tid)]
        if not picks:
            return faces, readings
        crops, kept = [], []
        for i, tid in picks:
            x1, y1, x2, y2 = (int(v) for v in faces[i]["bbox"])
            crop = frame[y1:y2, x1:x2]
            if crop.size:
                crops.append(crop)
                kept.append((i, tid))
        picks = kept
        res = self.demographics.classify(crops)
        if res is not None:
            for (_i, tid), g, a in zip(picks, res[0], res[1]):
                readings.append((tid, g, a))
        return faces, readings

    async def _run_emotion(self, cs: _Cam, frame: np.ndarray, ts: float,
                           track_boxes: Optional[Dict[int, Tuple[float, ...]]] = None) -> None:
        try:
            res, _w, ms = await self._worker.run(
                self._faces_job, frame, cs.camera_id, cs.zone, track_boxes or {}, prio=InferenceWorker.PRIO_SPECIALIST
            )
            cs.stage("emotion", ms)
            if self._cams.get(cs.camera_id) is not cs:
                return
            faces, readings = res if res else ([], [])
            for tid, g, a in readings:
                self._demo_visits.add(str(cs.camera_id), tid, g, a, ts)
            cs.faces, cs.faces_ts = faces, ts
            events = self.engine.on_faces(
                str(cs.camera_id), ts,
                [(tuple(float(v) for v in f["bbox"]), str(f["dominant_emotion"]), float(f["confidence"])) for f in faces],
            )
            self._queue_events(events)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            cs.error("emotion", e)
        finally:
            cs.emotion_busy = False

    @staticmethod
    def _emotion_view(cs: _Cam, ts: float) -> Dict[str, Any]:
        if not cs.faces or ts - cs.faces_ts > 30.0:
            return {}
        counts = Counter(f["dominant_emotion"] for f in cs.faces)
        return {"faces": cs.faces, "counts": dict(counts), "ts": cs.faces_ts}

    # ── events ──────────────────────────────────────────────────────
    @staticmethod
    def _ev_field(ev: Dict[str, Any], key: str) -> Any:
        """Engine events keep their details in ev["payload"]; fall back to the top level."""
        payload = ev.get("payload")
        if isinstance(payload, dict) and key in payload:
            return payload[key]
        return ev.get(key)

    def _fire_alert(self, cs: _Cam, ev: Dict[str, Any]) -> Dict[str, Any]:
        f = self._ev_field
        cls = str(f(ev, "class_name") or "fire").lower()
        return {
            "type": cls, "alert_type": cls, "confidence": f(ev, "peak_conf"), "zone": cs.zone,
            "bbox": f(ev, "bbox_xywh"), "action": f(ev, "action"), "incident_key": f(ev, "incident_key"),
            "camera_id": cs.camera_id, "timestamp": ev.get("ts"),
        }

    def _finish_visit_demographics(self, cs: _Cam, ev: Dict[str, Any]) -> None:
        try:
            tid = int(ev.get("track_id"))
        except (TypeError, ValueError):
            return
        res = self._demo_visits.finish(str(cs.camera_id), tid)
        if res is None:
            return
        payload = ev.get("payload") or {}
        first = payload.get("first_seen", res["first_ts"])
        last = payload.get("last_seen", ev.get("ts", res["last_ts"]))
        self._demo_rows.append({
            "camera_id": cs.camera_id, "zone": cs.zone, "track_id": tid, "global_id": ev.get("global_id"),
            "age_band": res["age_band"], "gender": res["gender"], "confidence": round(res["age_conf"], 4),
            "sample_count": res["sample_count"],
            "visit_start": datetime.fromtimestamp(float(first), timezone.utc),
            "visit_end": datetime.fromtimestamp(float(last), timezone.utc),
        })

    def _queue_events(self, events: List[Dict[str, Any]], cs_hint: Optional[_Cam] = None) -> None:
        for ev in events or []:
            if len(self._event_buffer) == self._event_buffer.maxlen:
                self._events_dropped += 1
            self._event_buffer.append(ev)
            try:
                cam = int(ev.get("camera_id"))
            except (TypeError, ValueError):
                cam = None
            cs = self._cams.get(cam) if cam is not None else None
            if cs is None and cs_hint is not None and cs_hint.camera_id == cam:
                cs = cs_hint  # camera being torn down
            if cs is not None:
                cs.pending_events.append(ev)
                if len(cs.pending_events) > 2000:
                    del cs.pending_events[:-2000]
            etype = ev.get("type")
            action = self._ev_field(ev, "action")
            if etype == "fire" and cs is not None:
                if action in ("start", "reopen"):
                    alert = self._fire_alert(cs, ev)
                    cs.fire_active = True
                    cs.pending_fire.append(alert)
                    self._fire_history.append(alert)
                    logger.warning(f"[Cam {cs.camera_id}] {alert['alert_type']} alert ({action}) conf={alert['confidence']}")
                    if action == "start":
                        self._capture_evidence(ev, cs)
                elif action == "clear":
                    cs.fire_active = False
            elif etype == "alert" and cs is not None and action in (None, "open"):
                self._capture_evidence(ev, cs)
            elif etype == "track_visit" and cs is not None and self.demographics is not None:
                self._finish_visit_demographics(cs, ev)
            publish_local("analytics_event", ev)
            if self._broadcast is not None and etype in BROADCAST_EVENT_TYPES:
                channel = "alerts" if etype in ("alert", "fire", "crowd_level") else "analytics"
                self._spawn(self._broadcast.broadcast(channel, f"analytics_{etype}", ev))

    def _publish_track_event(self, cam: int, e, gid: Optional[str]) -> None:
        payload = {"type": e.type, "camera_id": cam, "track_id": int(e.track_id), "ts": e.ts,
                   "global_id": gid, "track": e.track}
        publish_local("track_event", payload)
        if self._bus is not None and e.type in ("track_confirmed", "track_lost"):
            self._bus_queue.append((REDIS_TRACK_STREAM, f"{cam}:{e.track_id}", payload))

    def _handle_reid_events(self, events: List[Dict[str, Any]]) -> None:
        for ev in events or []:
            publish_local("reid_event", ev)
            if self._bus is not None:
                self._bus_queue.append((REDIS_ASSIGN_STREAM, str(ev.get("global_id")), ev))
            if ev.get("type") != "reid_assigned":
                if ev.get("type") == "reid_track_ended":
                    cs = self._cams.get(int(ev.get("camera_id", -1)))
                    if cs is not None:
                        cs.gid_emit_ts.pop(ev.get("global_id"), None)
                        if ev.get("track_id") is not None:
                            cs.track_thumbs.pop(int(ev.get("track_id")), None)
                continue
            gid, cam = ev.get("global_id"), int(ev.get("camera_id"))
            prev = self._gid_last_cam.get(gid)
            self._gid_last_cam[gid] = cam
            self._gid_last_cam.move_to_end(gid)
            while len(self._gid_last_cam) > 10_000:
                self._gid_last_cam.popitem(last=False)
            cs = self._cams.get(cam)
            if cs is None:
                continue
            tr = cs.tracker.get_track(int(ev.get("track_id")))
            bbox = [round(v, 1) for v in tr.bbox_xywh] if tr is not None else None
            cs.gid_emit_ts[gid] = float(ev.get("ts") or time.time())
            self._promote_thumb(gid, cs, int(ev.get("track_id")), float(ev.get("ts") or time.time()))
            cs.pending_reid.append({
                "global_id": gid, "camera_id": cam, "track_id": int(ev.get("track_id")), "bbox": bbox,
                "timestamp": ev.get("ts"), "source": ev.get("reason"), "similarity": ev.get("similarity"),
            })
            if len(cs.pending_reid) > 2000:
                del cs.pending_reid[:-2000]
            if prev is not None and prev != cam and ev.get("reason") != "new" and self._broadcast is not None:
                self._spawn(self._broadcast.push_reid_match(gid, cam, prev, ev.get("similarity"), ev.get("reason")))

    # ── background loops ────────────────────────────────────────────
    async def _dispatch_loop(self) -> None:
        period = 1.0 / CALLBACK_HZ
        hard_timeout = max(10.0, 10 * settings.CALLBACK_TIMEOUT_MS / 1000.0)
        while True:
            try:
                await asyncio.sleep(period)
                batch: Dict[int, CameraResult] = {}
                for cam, cs in list(self._cams.items()):
                    if cs.last_result is None:
                        continue
                    if cs.result_seq == cs.dispatched_seq and not (cs.pending_fire or cs.pending_reid or cs.pending_events):
                        continue
                    cs.dispatched_seq = cs.result_seq
                    batch[cam] = dataclasses.replace(
                        cs.last_result,
                        fire_alerts=cs.pending_fire, reid_matches=cs.pending_reid, events=cs.pending_events,
                    )
                    cs.pending_fire, cs.pending_reid, cs.pending_events = [], [], []
                self._update_global_state()
                if not batch:
                    continue
                for slot in self._callbacks:
                    if slot.task is not None and not slot.task.done():
                        slot.backlog = _merge_results(slot.backlog or {}, batch)
                        slot.busy_skips += 1
                        continue
                    payload = _merge_results(slot.backlog, batch) if slot.backlog else batch
                    slot.backlog = None
                    slot.task = asyncio.create_task(self._invoke(slot, payload, hard_timeout))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Pipeline dispatcher error: {e}")

    async def _invoke(self, slot: _CallbackSlot, payload: Dict[int, CameraResult], hard_timeout: float) -> None:
        t0 = time.perf_counter()
        try:
            res = slot.fn(payload, self.global_state)
            if asyncio.iscoroutine(res):
                await asyncio.wait_for(res, timeout=hard_timeout)
        except asyncio.TimeoutError:
            slot.failures += 1
            logger.warning(f"Pipeline callback {getattr(slot.fn, '__qualname__', slot.fn)} hung > {hard_timeout:.0f}s; cancelled")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            slot.failures += 1
            if slot.failures == 1 or slot.failures % 50 == 0:
                logger.error(f"Pipeline callback {getattr(slot.fn, '__qualname__', slot.fn)} failed: {e} (x{slot.failures})")
        ms = (time.perf_counter() - t0) * 1000.0
        slot.last_ms = round(ms, 1)
        if ms > settings.CALLBACK_TIMEOUT_MS:
            slot.slow_calls += 1

    def _update_global_state(self) -> None:
        gs = self.global_state
        active = {}
        for a in self.coordinator.active():
            active[a["global_id"]] = {
                "camera_id": a["camera_id"], "track_id": a["track_id"], "bbox": a["bbox"],
                "last_seen": a["last_seen"], "similarity": a["similarity"],
            }
        gs.active_tracks = active
        occ: Dict[str, int] = {}
        for cs in self._cams.values():
            occ[cs.zone] = occ.get(cs.zone, 0) + cs.confirmed_now
        gs.zone_occupancy = occ
        gs.total_persons_tracked = int(self.coordinator.gallery.counter)
        gs.fire_alert_active = any(cs.fire_active for cs in self._cams.values())
        vibe = self.engine.latest_vibe()
        gs.vibe_score = float(vibe.get("score") or 0.0) if vibe else 0.0
        gs.last_updated = time.time()

    async def _flush_demographics(self) -> None:
        """One DemographicSnapshot row per finished visit; cameras missing from the cameras table are skipped."""
        if not self._demo_rows:
            return
        rows = list(self._demo_rows)
        self._demo_rows.clear()
        try:
            from sqlalchemy import select

            from app.database import AsyncSessionLocal
            from app.models.analytics import DemographicSnapshot
            from app.models.camera import Camera

            async with AsyncSessionLocal() as session:
                cams = {r["camera_id"] for r in rows}
                known = set((await session.execute(select(Camera.id).where(Camera.id.in_(cams)))).scalars().all())
                for cid in cams - known:
                    if cid not in self._demo_skipped_cams:
                        self._demo_skipped_cams.add(cid)
                        logger.warning(f"[Cam {cid}] not in the cameras table; its demographic visits are not stored")
                n = 0
                for r in rows:
                    if r["camera_id"] not in known:
                        continue
                    session.add(DemographicSnapshot(
                        camera_id=r["camera_id"], zone=r["zone"], track_id=r["track_id"], global_id=r["global_id"],
                        age_band=r["age_band"], age_group=r["age_band"], gender=r["gender"],
                        estimated_gender=r["gender"], confidence=r["confidence"], sample_count=r["sample_count"],
                        count=1, visit_start=r["visit_start"], visit_end=r["visit_end"], timestamp=r["visit_end"],
                    ))
                    n += 1
                if n:
                    await session.commit()
                    self._demo_written += n
        except Exception as e:
            logger.warning(f"Demographic visits not persisted ({len(rows)}): {e}")

    async def _flush_events(self) -> None:
        await self._flush_demographics()
        if not self._event_buffer:
            return
        batch = list(self._event_buffer)
        self._event_buffer.clear()
        try:
            from app.analytics import zones_repo

            await zones_repo.persist_events(batch)
            self._events_persisted += len(batch)
        except Exception as e:  # persist_events never raises; import errors might
            logger.warning(f"Analytics events not persisted ({len(batch)}): {e}")

    async def _persist_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                await self._flush_events()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Event persister error: {e}")

    def _init_bus(self) -> None:
        if self._bus is not None:
            return
        try:
            from app.services.event_bus import RedisStreamBus, aioredis

            if aioredis is not None and settings.REDIS_URL:
                self._bus = RedisStreamBus(settings.REDIS_URL)
        except Exception as e:
            logger.info(f"Track events not mirrored to Redis: {e}")
            self._bus = None

    async def _bus_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(0.5)
                while self._bus_queue and self._bus is not None:
                    stream, key, payload = self._bus_queue.popleft()
                    await self._bus.publish(stream, key, payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Redis mirror error: {e}")

    async def _housekeeping_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(5.0)
                now = time.time()
                self.coordinator.maintain(now)
                for cs in self._cams.values():
                    stale = [g for g, t in cs.gid_emit_ts.items() if now - t > 120.0]
                    for g in stale:
                        cs.gid_emit_ts.pop(g, None)
                self._demo_visits.prune(DEMOGRAPHICS_STALE_S, now)
                self._check_memory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Pipeline housekeeping error: {e}")

    def _check_memory(self) -> None:
        if psutil is None:
            return
        pct = float(psutil.virtual_memory().percent)
        limit = float(settings.MEMORY_PRESSURE_PCT)
        scale = self._mem["scale"]
        if pct >= limit and scale > 0.2:
            scale = max(0.2, scale * 0.7)
        elif pct < limit - 10.0 and scale < 1.0:
            scale = min(1.0, scale / 0.7)
        if scale != self._mem["scale"]:
            logger.warning(f"Memory {pct:.0f}% (limit {limit:.0f}%): processing FPS scaled to {scale:.2f}x")
            for cs in self._cams.values():
                cs.fps_scale = scale
                stream = self.stream_manager._streams.get(cs.camera_id)
                if stream is not None:
                    stream.set_fps_target(max(1.0, cs.fps_target * scale))
        self._mem = {"pct": pct, "scale": scale, "pressure": scale < 1.0}

    # ── rendering ───────────────────────────────────────────────────
    def _overlay(self, cs: _Cam, confirmed, gids: Dict[int, Optional[str]], ts: float) -> Dict[str, Any]:
        return {
            "tracks": [(tuple(tr.xyxy), tr.track_id, gids.get(tr.track_id)) for tr in confirmed],
            "fire": [(b["xyxy"], b["class_name"], b["confidence"]) for b in cs.fire_boxes] if ts - cs.fire_ts <= OVERLAY_TTL_S else [],
            "fire_active": cs.fire_active,
            "products": [b["xyxy"] for b in cs.product_boxes] if ts - cs.product_ts <= max(OVERLAY_TTL_S, settings.PRODUCT_INTERVAL_S * 1.5) else [],
            "zones": cs.zones,
            "fps": cs.processed_fps(),
            "ts": ts,
        }

    @staticmethod
    def _draw(frame: np.ndarray, ov: Dict[str, Any]) -> np.ndarray:
        img = frame.copy()
        h, w = img.shape[:2]
        th = max(1, int(round(min(w, h) / 360)))
        fs = max(0.4, min(w, h) / 900)
        for z in ov.get("zones") or []:
            pts = np.array([[int(x * w), int(y * h)] for x, y in z.get("points") or []], np.int32)
            if len(pts) < 2:
                continue
            ztype = z.get("zone_type")
            col = (0, 200, 255) if ztype == "entry_line" else (60, 60, 255) if ztype == "restricted" else (255, 200, 0)
            cv2.polylines(img, [pts], z.get("zone_type") != "entry_line", col, th, cv2.LINE_AA)
            cv2.putText(img, str(z.get("name", "")), tuple(int(v) for v in pts[0]), cv2.FONT_HERSHEY_SIMPLEX,
                        fs * 0.8, col, th, cv2.LINE_AA)
        for xyxy in ov.get("products") or []:
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            cv2.rectangle(img, (x1, y1), (x2, y2), (200, 200, 200), 1)
        for (x1, y1, x2, y2), tid, gid in ov.get("tracks") or []:
            col = _gid_color(gid or f"t{tid}")
            p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
            cv2.rectangle(img, p1, p2, col, th + 1)
            label = f"{gid}" if gid else f"#{tid}"
            (tw, tht), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
            ty = max(tht + 4, p1[1])
            cv2.rectangle(img, (p1[0], ty - tht - 6), (p1[0] + tw + 6, ty), col, -1)
            cv2.putText(img, label, (p1[0] + 3, ty - 4), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th, cv2.LINE_AA)
        for xyxy, cls, conf in ov.get("fire") or []:
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), th + 1)
            cv2.putText(img, f"{cls} {conf:.2f}", (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 255), th, cv2.LINE_AA)
        hud = f"people {len(ov.get('tracks') or [])}  {ov.get('fps', 0):.1f} fps"
        if ov.get("fire_active"):
            hud += "  FIRE/SMOKE ALERT"
        cv2.putText(img, hud, (8, int(24 * fs * 1.6)), cv2.FONT_HERSHEY_SIMPLEX, fs * 1.1, (0, 0, 0), th + 2, cv2.LINE_AA)
        cv2.putText(img, hud, (8, int(24 * fs * 1.6)), cv2.FONT_HERSHEY_SIMPLEX, fs * 1.1,
                    (0, 0, 255) if ov.get("fire_active") else (255, 255, 255), th, cv2.LINE_AA)
        return img

    def _render_job(self, cam: int, frame: np.ndarray, ov: Dict[str, Any], encode: bool) -> Tuple[Optional[bytes], float]:
        t0 = time.perf_counter()
        img = self._draw(frame, ov)
        rec = self._recorders.get(cam)
        if rec is not None:
            writer, _path, lock = rec[:3]
            with lock:
                size = rec[3] if len(rec) > 3 else None
                if size and (img.shape[1], img.shape[0]) != size:
                    writer.write(cv2.resize(img, size))
                else:
                    writer.write(img)
        jpeg = None
        if encode:
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            jpeg = buf.tobytes() if ok else None
        return jpeg, (time.perf_counter() - t0) * 1000.0

    def _maybe_render(self, cs: _Cam) -> None:
        watching = time.time() - cs.viewer_ts < VIEWER_TTL_S
        recording = cs.camera_id in self._recorders
        if not (watching or recording) or cs.frame is None:
            return
        if cs.render_busy:
            cs.render_skipped += 1
            return
        cs.render_busy = True
        fut = asyncio.get_running_loop().run_in_executor(
            self._render_pool, self._render_job, cs.camera_id, cs.frame, cs.overlay, watching
        )

        def _done(f) -> None:
            cs.render_busy = False
            try:
                jpeg, ms = f.result()
            except Exception as e:
                cs.error("render", e)
                return
            cs.stage("render", ms)
            if jpeg is not None:
                cs.jpeg = jpeg
                cs.jpeg_seq += 1

        fut.add_done_callback(_done)

    def get_latest_annotated_jpeg(self, camera_id: int) -> Optional[bytes]:
        """Newest annotated JPEG; marks the feed as watched so rendering continues."""
        got = self.get_latest_annotated_jpeg_seq(camera_id, 0)
        return got[1] if got else None

    def get_latest_clean_jpeg(self, camera_id: int) -> Optional[bytes]:
        """Newest raw frame without overlays (background for heatmaps / zone drawing)."""
        cs = self._cams.get(int(camera_id))
        frame = cs.frame if cs is not None else None
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else None

    def get_latest_annotated_jpeg_seq(self, camera_id: int, after_seq: int = 0) -> Optional[Tuple[int, bytes]]:
        """``(seq, jpeg)`` if a frame newer than ``after_seq`` exists (for MJPEG without duplicates)."""
        cs = self._cams.get(int(camera_id))
        if cs is None:
            return None
        cs.viewer_ts = time.time()
        if cs.jpeg is None and cs.frame is not None and cs.overlay is not None:
            try:
                img = self._draw(cs.frame, cs.overlay)
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    cs.jpeg, cs.jpeg_seq = buf.tobytes(), cs.jpeg_seq + 1
            except Exception as e:
                cs.error("render", e)
        if cs.jpeg is None or cs.jpeg_seq <= after_seq:
            return None
        return cs.jpeg_seq, cs.jpeg

    # ── recording ───────────────────────────────────────────────────
    def _stop_recording_impl(self, camera_id: int) -> Optional[str]:
        rec = self._recorders.pop(camera_id, None)
        if not rec:
            return None
        writer, path, lock = rec[0], rec[1], rec[2]
        with lock:
            try:
                writer.release()
            except Exception:
                pass
        if not video_readable(path):
            logger.error(f"Recording {path} cannot be read back (empty or unsupported codec)")
        self._flush_detection_log(camera_id)
        return str(path)

    def _flush_detection_log(self, camera_id: int) -> None:
        log_data = self._detection_logs.pop(camera_id, None)
        if not log_data:
            return
        log_path = log_data.pop("log_path", None)
        if not log_path:
            return
        log_data["end_time"] = datetime.now(timezone.utc).isoformat()
        log_data["total_frames"] = len(log_data.get("frames", []))
        try:
            with open(str(log_path), "w", encoding="utf-8") as f:
                json.dump(log_data, f, default=str)
            logger.info(f"Detection log saved: {log_path} ({log_data['total_frames']} frames)")
        except Exception as e:
            logger.error(f"Failed to save detection log: {e}")

    def _log_detections(self, cs: _Cam, r: CameraResult) -> None:
        log = self._detection_logs.get(cs.camera_id)
        if log is None or len(log["frames"]) >= 200_000:
            return
        log["frames"].append({
            "frame_number": r.frame_number,
            "timestamp": r.timestamp,
            "detection_count": len(r.tracks),
            "detections": [
                {"track_id": t["track_id"], "class_name": "person", "confidence": t["confidence"],
                 "bbox": t["bbox"], "global_id": t["global_id"], "region": cs.zone}
                for t in r.tracks
            ],
        })

    def start_recording(self, camera_id: int) -> Dict[str, Any]:
        """Record this camera's annotated feed (processed frames) to FOOTAGE_DIR + a JSON detection log."""
        camera_id = int(camera_id)
        if camera_id in self._recorders:
            return {"recording": True, "message": f"Camera {camera_id} already recording"}
        cs = self._cams.get(camera_id)
        if cs is None:
            return {"recording": False, "error": f"Camera {camera_id} is not active"}
        stats = self.stream_manager.get_stats(camera_id)
        w, h = (stats.resolution if stats and stats.resolution and stats.resolution[0] else (0, 0))[:2]
        if cs.frame is not None:
            h, w = cs.frame.shape[:2]
        if w <= 0 or h <= 0:
            return {"recording": False, "error": "Camera has no frames yet; try again in a moment"}
        footage_dir = Path(settings.FOOTAGE_DIR)
        footage_dir.mkdir(parents=True, exist_ok=True)
        out_path = footage_dir / f"camera_{camera_id}_{int(time.time())}.mp4"
        fps = max(1.0, cs.processed_fps() or cs.fps_target)
        writer, codec = open_video_writer(out_path, fps, (int(w), int(h)))
        if writer is None:
            logger.error(f"Failed to create recorder for camera {camera_id} (tried {', '.join(RECORDER_CODECS)})")
            return {"recording": False, "error": "Failed to create video file"}
        logger.info(f"Camera {camera_id} recorder codec: {codec}")
        self._recorders[camera_id] = (writer, out_path, threading.Lock(), (int(w), int(h)))
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._logs_dir / f"{out_path.stem}.json"
        self._detection_logs[camera_id] = {
            "log_path": log_path, "frames": [], "video_file": str(out_path), "camera_id": camera_id,
            "model": Path(self._detector_model).name, "fps": fps,
            "start_time": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"Recording started for camera {camera_id} -> {out_path}")
        return {"recording": True, "path": str(out_path), "log_path": str(log_path), "camera_id": camera_id}

    def stop_recording(self, camera_id: int) -> Dict[str, Any]:
        camera_id = int(camera_id)
        info = self._detection_logs.get(camera_id)
        log_path = str(info["log_path"]) if info else None
        path = self._stop_recording_impl(camera_id)
        if path:
            logger.info(f"Recording stopped for camera {camera_id} -> {path}")
            return {"recording": False, "saved": path, "log_path": log_path, "camera_id": camera_id}
        return {"recording": False, "message": f"Camera {camera_id} was not recording"}

    def get_recording_status(self) -> Dict[str, Any]:
        cams = list(self._recorders.keys())
        return {"recording_cameras": cams, "recording": cams}

    # ── Re-ID gallery hooks used by persistence ─────────────────────
    def get_and_clear_pending_embeddings(self) -> List[Dict[str, Any]]:
        return self.coordinator.drain_vectors()

    def get_dirty_identities(self) -> List[Dict[str, Any]]:
        return self.coordinator.drain_dirty_identities()

    def warm_reid_gallery(self, rows: List[Dict[str, Any]]) -> int:
        info = self.coordinator.warm(rows)
        logger.info(
            f"Re-ID gallery warm start: {info['identities']} identities restored from {info['rows']} embedding rows "
            f"(next ID counter {info['counter']})"
        )
        self.global_state.total_persons_tracked = int(self.coordinator.gallery.counter)
        return int(info["identities"])

    @property
    def _latest_vibe(self) -> Optional[Dict[str, Any]]:
        return self.engine.latest_vibe()

    # ── read API ────────────────────────────────────────────────────
    def get_latest_results(self, camera_id: Optional[int] = None) -> Any:
        if camera_id is not None:
            cs = self._cams.get(int(camera_id))
            return cs.last_result if cs else None
        return {cam: cs.last_result for cam, cs in self._cams.items() if cs.last_result is not None}

    def _model_status(self, cs: _Cam) -> Dict[str, Any]:
        return {
            "person_detector": self.detector.name if self.detector else None,
            "reid": bool(self._reid_ready and cs.enable_reid),
            "fire": self.fire_model is not None,
            "product": self.product_model is not None,
            "emotion": self.emotion is not None,
        }

    def _models_info(self) -> Dict[str, Any]:
        def spec(m, key):
            if m is not None:
                return {"status": "loaded", "model": m.name}
            return {"status": "error" if key in self._model_errors else "not_loaded", "error": self._model_errors.get(key)}

        return {
            "person": ({"status": "loaded", **self.detector.info()} if self.detector else
                       {"status": "error" if "person" in self._model_errors else "not_loaded",
                        "model": Path(self._detector_model).name, "error": self._model_errors.get("person")}),
            "reid": ({"status": "loaded", **self.reid.info()} if self._reid_ready else
                     {"status": "error" if "reid" in self._model_errors else "not_loaded", "error": self._model_errors.get("reid")}),
            "fire": spec(self.fire_model, "fire"),
            "product": spec(self.product_model, "product"),
            "emotion": ({"status": "loaded"} if self.emotion is not None else
                        {"status": "error" if "emotion" in self._model_errors else "not_loaded", "error": self._model_errors.get("emotion")}),
        }

    def _camera_health(self, cs: _Cam) -> Dict[str, Any]:
        s = self.stream_manager.get_stats(cs.camera_id)
        stream = self.stream_manager._streams.get(cs.camera_id)
        stages = {}
        for name, vals in cs.stages.items():
            if vals:
                stages[name] = {"p50": _pct(vals, 0.5), "p95": _pct(vals, 0.95), "max": round(max(vals), 1), "n": len(vals)}
        recent = [m for t, m in cs.errors if t >= time.time() - 60.0]
        return {
            "camera_id": cs.camera_id,
            "zone": cs.zone,
            "connected": bool(s and s.is_connected),
            "stream_error": s.error_message if s else None,
            "last_frame_time": cs.last_frame_ts,
            "last_frame_age_s": round(time.time() - cs.last_frame_ts, 2) if cs.last_frame_ts else None,
            "target_fps": round(cs.fps_target * cs.fps_scale, 2),
            "processed_fps": cs.processed_fps(),
            "source_fps": stream.source_fps if stream else None,
            "frames_processed": cs.frames_processed,
            "frames_read": s.frames_read if s else 0,
            "frames_dropped": s.frames_dropped if s else 0,
            "reconnects": s.reconnect_count if s else 0,
            "active_tracks": cs.confirmed_now,
            "tracker_buffer_frames": cs.tracker.track_buffer_frames,
            "reid_enabled": cs.enable_reid,
            "loop": cs.loop,
            "finished": cs.finished,
            "finished_at": cs.finished_at,
            "loops_completed": s.loops_completed if s else 0,
            "evidence_ring": cs.ring.stats() if cs.ring is not None else None,
            "errors_last_min": len(recent),
            "last_error": recent[-1] if recent else None,
            "render_skipped": cs.render_skipped,
            "watched": time.time() - cs.viewer_ts < VIEWER_TTL_S,
            "stage_latency_ms": stages,
        }

    def get_health(self) -> Dict[str, Any]:
        cams = {cam: self._camera_health(cs) for cam, cs in self._cams.items()}
        errors = sum(c["errors_last_min"] for c in cams.values())
        disconnected = [c for c, h in cams.items() if not h["connected"] and not h["finished"]]
        if self.state == PipelineState.ERROR:
            status = "error"
        elif self.state != PipelineState.RUNNING:
            status = "idle"
        elif cams and all(h["finished"] for h in cams.values()):
            status = "finished"
        elif disconnected or errors:
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "state": self.state.value,
            "device": self.device,
            "target_fps": self.processing_fps,
            "cameras": cams,
            "models": self._models_info(),
            "inference_worker": self._worker.stats() if self._worker else {"alive": False},
            "reid": {**self.coordinator.snapshot(), "quality_gate": dict(self._reid_gate)},
            "callbacks": [
                {"name": getattr(s.fn, "__qualname__", str(s.fn)), "busy_skips": s.busy_skips,
                 "slow_calls": s.slow_calls, "failures": s.failures, "last_ms": s.last_ms}
                for s in self._callbacks
            ],
            "events": {"buffered": len(self._event_buffer), "persisted": self._events_persisted,
                       "dropped": self._events_dropped},
            "memory": dict(self._mem),
            "timestamp": time.time(),
        }

    def get_status(self) -> Dict[str, Any]:
        stream_stats = self.stream_manager.get_all_stats()
        det_name = self.detector.name if self.detector else Path(self._detector_model).name
        return {
            "state": self.state.value,
            "cameras": {
                "total": self.stream_manager.total_count,
                "active": self.stream_manager.active_count,
                "max": self.max_cameras,
                "zones": dict(self._camera_zones),
                "stats": {
                    cam_id: {
                        "connected": s.is_connected,
                        "fps": self._cams[cam_id].processed_fps() if cam_id in self._cams else s.fps_actual,
                        "frames_read": s.frames_read,
                        "frames_dropped": s.frames_dropped,
                        "resolution": s.resolution,
                        "uptime": round(s.uptime_seconds, 1),
                        "reconnects": s.reconnect_count,
                        "loop": self._cams[cam_id].loop if cam_id in self._cams else False,
                        "finished": bool(s.finished or (cam_id in self._cams and self._cams[cam_id].finished)),
                        "finished_at": s.finished_at,
                        "loops_completed": s.loops_completed,
                    }
                    for cam_id, s in stream_stats.items()
                },
                "reid_enabled": {c: cs.enable_reid for c, cs in self._cams.items()},
                "tracker_modes": {c: "bytetrack" for c in self._cams},
                "models": {c: {"paths": [det_name], "mode": "single", "label": det_name} for c in self._cams},
            },
            "ai_modules": {
                "detector": {
                    "status": "loaded" if self.detector else ("error" if "person" in self._model_errors else "idle"),
                    "active_models": [det_name],
                    "loaded_models": [det_name] if self.detector else [],
                    "backend": self.detector.backend if self.detector else None,
                    "device": self.device,
                },
                "tracker": "bytetrack",
                "reid": {
                    "status": "loaded" if self._reid_ready else ("error" if "reid" in self._model_errors else "idle"),
                    "model": self._reid_arch,
                    "weights": Path(settings.REID_WEIGHTS_PATH).name,
                    "threshold": self.coordinator.cfg.t_match,
                    "t_update": self.coordinator.cfg.t_update,
                    "unique_identities": len(self.coordinator.gallery.identities()),
                    "max_identities": self.coordinator.cfg.max_identities,
                    "enabled_feeds": sum(1 for cs in self._cams.values() if cs.enable_reid),
                },
                "emotion": "enabled" if self.emotion is not None else "disabled",
                "demographics": {
                    "status": "enabled" if self.demographics is not None else (
                        "model_not_installed" if self._model_errors.get("demographics") == "model_not_installed"
                        else "disabled"),
                    "open_visits": len(self._demo_visits),
                    "visits_written": self._demo_written,
                },
                "fire": "enabled" if self.fire_model is not None else "disabled",
                "product": "enabled" if self.product_model is not None else "disabled",
                "shelf": "zones",
                "checkout": "zones",
            },
            "global_state": {
                "total_persons_tracked": self.global_state.total_persons_tracked,
                "active_tracks": len(self.global_state.active_tracks),
                "zone_occupancy": self.global_state.zone_occupancy,
                "fire_alert": self.global_state.fire_alert_active,
                "vibe_score": round(self.global_state.vibe_score, 1),
            },
            "reid_scope": "global",
            "processing": {
                "target_fps": self.processing_fps,
                "frame_buffer_size": 1,
                "default_skip_frames": 0,
                "fire_interval_frames": settings.FIRE_INTERVAL_FRAMES,
                "product_interval_s": settings.PRODUCT_INTERVAL_S,
                "emotion_interval_frames": settings.EMOTION_ANALYTICS_INTERVAL_FRAMES,
                "callback_hz": CALLBACK_HZ,
                "callback_timeout_ms": settings.CALLBACK_TIMEOUT_MS,
                "frame_counts": {c: cs.frames_processed for c, cs in self._cams.items()},
                "total_detections_processed": self._total_detections,
                "memory_scale": self._mem["scale"],
            },
        }

    def get_analytics_snapshot(self) -> Dict[str, Any]:
        """Live analytics for the REST routers, built from the zone engine (no invented values)."""
        snaps = {cam: self.engine.snapshot(str(cam)) for cam in self._cams}
        crowd_zones, shelf, lanes, per_zone_emotion = [], [], [], {}
        sentiments, samples = [], 0
        for cam, sn in snaps.items():
            for z in sn.get("zones", []):
                if z.get("zone_type") == "crowd":
                    crowd_zones.append({"camera_id": cam, "zone_id": z["id"], "zone": z["name"], "count": z.get("count"),
                                        "capacity": z.get("capacity"), "pct": z.get("pct"), "level": z.get("level")})
                if z.get("zone_type") == "shelf":
                    md = z.get("median_dwell_s")
                    shelf.append({"camera_id": cam, "zone_id": z["id"], "zone": z["name"], "visits": z.get("visits"),
                                  "engaged_visits": z.get("engaged_visits"), "median_dwell_s": md, "passers": z.get("passers"),
                                  "attention": (z.get("engaged_visits") or 0) * (md or 0.0)})
            for ln in sn.get("lanes", []):
                lanes.append({"camera_id": cam, **ln})
            emo = sn.get("emotion") or {}
            if emo.get("counts"):
                per_zone_emotion[self._camera_zones.get(cam, str(cam))] = emo
                samples += sum(emo["counts"].values())
                if emo.get("sentiment") is not None:
                    sentiments.append(float(emo["sentiment"]))
        top = max((s["attention"] for s in shelf), default=0.0)
        for s in shelf:
            s["attention_score"] = round(100.0 * s["attention"] / top, 1) if top > 0 else 0.0
        shelf.sort(key=lambda s: -s["attention"])
        active_fire = [{"camera_id": cam, **sn["fire"]} for cam, sn in snaps.items() if (sn.get("fire") or {}).get("active")]
        vibe = self.engine.latest_vibe()
        return {
            "timestamp": time.time(),
            "cameras": list(self._cams.keys()),
            "detections": {
                "active_frame": sum(cs.confirmed_now for cs in self._cams.values()),
                "total_processed": self._total_detections,
                "frames_processed": sum(cs.frames_processed for cs in self._cams.values()),
                "frame_counts_by_camera": {c: cs.frames_processed for c, cs in self._cams.items()},
            },
            "crowd": {
                "zones": crowd_zones,
                "total_occupancy": sum(cs.confirmed_now for cs in self._cams.values()),
                "critical_zones": [z["zone"] for z in crowd_zones if z.get("level") in ("high", "critical")],
                "status": "ok" if crowd_zones else "no_data",
            },
            "fire": {"active_alerts": active_fire, "history": list(self._fire_history)[-50:], "active": bool(active_fire)},
            "emotions": {
                "per_zone": per_zone_emotion,
                "overall_sentiment": round(sum(sentiments) / len(sentiments), 3) if sentiments else None,
                "samples": samples,
                "status": "ok" if samples else "no_data",
            },
            "shelf": {"rankings": shelf, "per_camera": {c: [s for s in shelf if s["camera_id"] == c] for c in self._cams},
                      "status": "ok" if shelf else "no_data"},
            "checkout": {
                "lanes": lanes,
                "summary": {"lanes": len(lanes), "waiting": sum(ln.get("waiting") or 0 for ln in lanes),
                            "served": sum(ln.get("served") or 0 for ln in lanes),
                            "abandoned": sum(ln.get("abandoned") or 0 for ln in lanes)},
                "status": "ok" if lanes else "no_data",
            },
            "vibe": vibe if vibe else {"status": "no_data"},
            "reid": {"total_persons_tracked": self.global_state.total_persons_tracked,
                     "active_tracks": len(self.global_state.active_tracks)},
        }
