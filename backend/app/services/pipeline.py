"""
OmniTrack AI — Multi-Camera Synchronized Processing Pipeline
═══════════════════════════════════════════════════════════════

HOW IT WORKS (for a non-CV person):
  Think of this as the "brain" of the system. Each camera is like an eye.
  The pipeline does:

  1. CAPTURE  → Grabs frames from ALL cameras simultaneously (threaded)
  2. DETECT   → Runs YOLOv8 on each frame to find all people (bounding boxes)
  3. TRACK    → Assigns each person a track ID so we follow them across frames
  4. RE-ID    → When a person disappears from Cam1 and appears on Cam2,
                Re-ID matches them by their visual "fingerprint" (512-d embedding)
  5. ANALYZE  → Runs all analytics in parallel:
                - Shelf engagement (how long did they browse?)
                - Crowd density (how packed is each zone?)
                - Emotion recognition (are customers happy?)
                - Fire/smoke detection (safety!)
                - Checkout queues (how long is the wait?)
  6. PERSIST  → Saves results to database & pushes real-time alerts via WebSocket

  All cameras run in PARALLEL — synchronized by a central clock.
  Cross-camera identity matching happens continuously via the Re-ID gallery.

GLOBAL ID & CROSS-CAMERA DATA (how the store gap is filled):
  - Each camera has its own ByteTrack tracker → local track IDs (1, 2, 3…) per feed.
  - One shared Re-ID gallery (in memory, optionally pgvector in DB): 512-d embedding per person.
  - For every detected person, we crop the bounding box, extract an embedding (Torchreid), and
    search the gallery by cosine similarity. If similarity ≥ threshold → same global_id (e.g. PERSON-00042).
    If no match → new global_id, and we add that embedding to the gallery.
  - So: same person on Cam 1 (entrance) and Cam 2 (aisle) gets the same global_id. Data from every
    camera is merged in global_state: active_tracks[global_id] = { camera_id, last_seen, bbox };
    zone_occupancy[zone] = count; vibe_score aggregates all cameras. The dashboard/API can answer
    “where is PERSON-00042?” or “how many unique people in the store?” from this shared state.

WHAT YOU NEED:
  - Camera RTSP URLs (from store IP cameras)
  - OR just use a video file for testing: pipeline.add_camera(1, "test_video.mp4")
  - GPU recommended for real-time (but CPU works at ~5-8 FPS per camera)
"""

import asyncio
import base64
import time
import json
import inspect
import numpy as np
import cv2
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Callable, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime, timezone
from loguru import logger
import threading
from enum import Enum

# AI Modules
from app.ai.detector import PersonDetector, BBox
from app.ai.tracker import MultiObjectTracker
from app.ai.reid import PersonReID
from app.ai.emotion import EmotionRecognizer
from app.ai.fire_detector import FireSmokeDetector  # alias for FireDetector
from app.ai.crowd_density import CrowdDensityEstimator
from app.ai.shelf_analytics import ShelfEngagementTracker  # alias for ShelfAnalytics
from app.ai.checkout_analytics import CheckoutAnalyzer  # alias for CheckoutAnalytics
from app.ai.store_vibe import StoreVibeEngine

# Stream Manager
from app.services.stream_manager import StreamManager, StreamConfig, StreamType
from app.config import settings, resolved_footage_dir, resolved_logs_dir
from pathlib import Path
from app.services.event_bus import EventBus, NullBus

if TYPE_CHECKING:
    from app.services.broadcast import BroadcastService

_MAX_REID_SNAPSHOT_CACHE = 400
_REID_WS_EMIT_MIN_INTERVAL_S = 1.5


class PipelineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class CameraResult:
    """Results from processing a single camera frame."""
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
    tick_id: int = 0


@dataclass
class GlobalState:
    """
    Shared state across ALL cameras — this is the magic of synchronization.
    
    The Re-ID gallery is GLOBAL: when Cam1 sees a person, their embedding
    goes into the shared gallery. When Cam2 sees someone similar, it finds
    the match. This gives cross-camera tracking.
    """
    total_persons_tracked: int = 0
    active_tracks: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # global_id (e.g. PERSON-00042) → info
    zone_occupancy: Dict[str, int] = field(default_factory=dict)  # zone → person count
    fire_alert_active: bool = False
    vibe_score: float = 0.0
    last_updated: float = 0.0


class ProcessingPipeline:
    """
    Multi-camera synchronized processing pipeline.
    
    Architecture:
    ┌──────────────────────────────────────────────────────────────────┐
    │                     PROCESSING PIPELINE                         │
    │                                                                  │
    │  ┌─────────┐  ┌─────────┐  ┌─────────┐                        │
    │  │  Cam 1  │  │  Cam 2  │  │  Cam N  │   ← Parallel capture   │
    │  └────┬────┘  └────┬────┘  └────┬────┘                        │
    │       │            │            │                              │
    │       ▼            ▼            ▼                              │
    │  ┌─────────────────────────────────────┐                      │
    │  │   YOLO Detection (batched/parallel) │  ← GPU-accelerated   │
    │  └─────────────────┬───────────────────┘                      │
    │                    │                                           │
    │       ┌────────────┼────────────┐                              │
    │       ▼            ▼            ▼                              │
    │  ┌─────────┐  ┌─────────┐  ┌─────────┐                      │
    │  │ Tracker │  │ Tracker │  │ Tracker │  ← Per-camera          │
    │  └────┬────┘  └────┬────┘  └────┬────┘                        │
    │       │            │            │                              │
    │       ▼            ▼            ▼                              │
    │  ┌─────────────────────────────────────┐                      │
    │  │   GLOBAL Re-ID Gallery (shared)     │  ← Cross-camera      │
    │  │   Matches identities across cameras │    identity matching  │
    │  └─────────────────┬───────────────────┘                      │
    │                    │                                           │
    │       ┌────────────┼────────────┐                              │
    │       ▼            ▼            ▼                              │
    │  ┌─────────┐ ┌──────────┐ ┌─────────┐                        │
    │  │ Emotion │ │   Fire   │ │  Shelf  │  ← Parallel analytics  │
    │  │ + Demo  │ │  Detect  │ │ + Crowd │                        │
    │  └─────────┘ └──────────┘ └─────────┘                        │
    │                    │                                           │
    │                    ▼                                           │
    │  ┌─────────────────────────────────────┐                      │
    │  │   Store Vibe Engine (aggregator)    │  ← Global score      │
    │  └─────────────────┬───────────────────┘                      │
    │                    │                                           │
    │                    ▼                                           │
    │             DB + WebSocket Push                                │
    └──────────────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        detector_model: str = "yolov8n.pt",
        reid_model: str = "osnet_x1_0",
        fire_model: str = "fire-smoke.pt",
        device: str = "auto",
        confidence: float = 0.5,
        processing_fps: int = 15,     # How many frames/sec to process per camera
        reid_threshold: float = 0.6,
        reid_embeddings_per_id: int = 5,
        enable_emotions: bool = True,
        enable_shelf: bool = True,
        enable_checkout: bool = True,
    ):
        self.state = PipelineState.IDLE
        self.processing_fps = processing_fps
        self.global_state = GlobalState()

        # --- Stream Manager (handles all camera connections) ---
        self.stream_manager = StreamManager()

        # --- AI Modules (shared across all cameras) ---
        logger.info("Initializing AI modules...")

        self._detector_model = detector_model
        self._detector_confidence = confidence
        self._detector_device = device
        # Cache of detectors by model path (allows per-camera model selection)
        self._detectors: Dict[str, PersonDetector] = {}
        self._camera_models: Dict[int, str] = {}  # camera_id -> model_path
        # Initialize default detector
        self.detector = self._get_or_create_detector(detector_model)
        # Per-camera trackers: each feed has its own ByteTrack state (local track IDs per camera).
        # Global identity across cameras is resolved by Re-ID, not by the tracker.
        self._trackers: Dict[int, MultiObjectTracker] = {}
        self.reid = PersonReID(
            model_name=reid_model,
            device=device if device != "auto" else "cpu",
            similarity_threshold=reid_threshold,
            max_embeddings_per_id=reid_embeddings_per_id,
        )
        # Temporal consistency: (camera_id, track_id) -> last global_id (avoids flicker when face not visible)
        self._last_global_by_track: Dict[Tuple[int, int], str] = {}

        # Optional modules
        self._enable_emotions = enable_emotions
        self._enable_shelf = enable_shelf
        self._enable_checkout = enable_checkout

        # Fire/smoke: lazy-loaded per checkpoint path; inference only when enable_fire=True for that camera.
        self._fire_model_path = fire_model  # default from settings when no per-feed override
        self._camera_fire_enabled: Dict[int, bool] = {}
        self._camera_fire_weights_path: Dict[int, str] = {}  # camera_id -> resolved .pt path
        self._fire_detectors: Dict[str, FireSmokeDetector] = {}  # resolved path -> detector

        self.emotion = EmotionRecognizer() if enable_emotions else None
        self.shelf_tracker = ShelfEngagementTracker() if enable_shelf else None
        self.checkout = CheckoutAnalyzer() if enable_checkout else None
        self.crowd = CrowdDensityEstimator()
        self.vibe_engine = StoreVibeEngine()
        self.event_bus: EventBus = NullBus()
        self.vector_store = None
        self.memory_guard = None
        self.storage_guard = None

        # --- Processing state ---
        self._processing_task: Optional[asyncio.Task] = None
        self._frame_counts: Dict[int, int] = {}
        self._results_buffer: Dict[int, CameraResult] = {}
        self._callbacks: List[Callable] = []  # Real-time result callbacks
        self._lock = asyncio.Lock()
        self._reid_merge_lock = asyncio.Lock()
        # Latest annotated frame (JPEG bytes) per camera for live MJPEG stream
        self._latest_annotated_jpeg: Dict[int, bytes] = {}
        self._jpeg_lock = threading.Lock()
        # Per-camera recording: camera_id -> (VideoWriter | None, output_path). Writer is
        # created on first processed frame so dimensions match decoded frames (stats.resolution
        # is pre-downscale and would corrupt MP4 if used for VideoWriter).
        self._recorders: Dict[int, tuple] = {}
        self._recorder_target_wh: Dict[int, Tuple[int, int]] = {}
        # Product YOLO overlay cache (throttled inference; boxes persist between runs)
        self._last_product_overlay: Dict[int, List[Dict[str, Any]]] = {}
        # Per-camera detection logs: camera_id -> {"log_path": Path, "frames": []}
        self._detection_logs: Dict[int, Dict[str, Any]] = {}
        # Ensure logs directory exists (backend-relative, not process cwd)
        self._logs_dir = resolved_logs_dir()
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        # camera_id -> zone_name (set at add_camera, used by analytics snapshot)
        self._camera_zones: Dict[int, str] = {}
        # Persisted for DB FK ensure on each tick (detections/embeddings reference cameras.id)
        self._camera_sources: Dict[int, str] = {}
        self._camera_fps: Dict[int, float] = {}
        # Detections counter — powers dashboard "total_detections_today"
        self._total_detections: int = 0
        # Last-computed store vibe dict, so routers can serve without recomputing
        self._latest_vibe: Optional[Dict[str, Any]] = None
        # Re-ID embeddings produced during the current tick — drained by persistence
        # callback so they land in pgvector. Kept off reid_matches to keep the WS
        # payload small (each vector is ~2 KB).
        self._pending_embeddings: List[Dict[str, Any]] = []
        self._pending_embeddings_lock = threading.Lock()
        # First-seen timestamps per global_id so journey legs get a real entry_time
        self._journey_leg_start: Dict[Tuple[str, int, str], float] = {}
        # Live WS + Cross-feed UI: optional broadcaster, per-camera Re-ID toggle, transition tracking
        self._broadcast: Optional["BroadcastService"] = None
        self._camera_reid_enabled: Dict[int, bool] = {}
        self._global_id_last_camera: Dict[str, int] = {}
        self._reid_snapshots: "OrderedDict[Tuple[str, int], str]" = OrderedDict()
        self._last_reid_ws_emit: Dict[str, float] = {}

        # Lifecycle hooks: external observers (e.g. audit log writer) register
        # callables that fire on pipeline events. Hooks are async-friendly.
        self._lifecycle_hooks: List[Callable[[str, Dict[str, Any]], Any]] = []

        logger.info(f"Pipeline initialized | device={device} | modules: "
                     f"emotion={enable_emotions} fire=lazy-per-feed "
                     f"shelf={enable_shelf} checkout={enable_checkout}")

    # ─────────────────────────────────────────────────────────────
    # CAMERA MANAGEMENT
    # ─────────────────────────────────────────────────────────────

    def _get_or_create_detector(self, model_path: str) -> PersonDetector:
        """Get cached detector or create new one for the given model path."""
        if model_path not in self._detectors:
            logger.info(f"Creating detector for model: {model_path}")
            self._detectors[model_path] = PersonDetector(
                model_path=model_path,
                confidence=self._detector_confidence,
                device=self._detector_device,
                person_class_only=True,
            )
        return self._detectors[model_path]

    def _get_product_detector(self) -> Optional[PersonDetector]:
        """Second YOLO for retail/product classes; None if PRODUCT_YOLO_PATH unset."""
        path = (getattr(settings, "PRODUCT_YOLO_PATH", None) or "").strip()
        if not path:
            return None
        cache_key = f"__product_yolo__:{path}"
        if cache_key not in self._detectors:
            conf = float(getattr(settings, "PRODUCT_YOLO_CONFIDENCE", 0.45))
            nms = float(getattr(settings, "NMS_THRESHOLD", 0.45))
            self._detectors[cache_key] = PersonDetector(
                model_path=path,
                confidence=conf,
                nms_threshold=nms,
                device=self._detector_device,
                person_class_only=False,
            )
        return self._detectors[cache_key]

    def _get_or_create_fire_detector(self, path: str) -> Optional[FireSmokeDetector]:
        """Load/cache a fire/smoke YOLO by resolved path (supports multiple weights across feeds)."""
        if not path:
            return None
        if path in self._fire_detectors:
            return self._fire_detectors[path]
        conf = float(getattr(settings, "FIRE_DETECTION_CONFIDENCE", 0.58))
        logger.info(f"Loading fire/smoke model ({path}, conf={conf})…")
        det = FireSmokeDetector(
            model_path=path,
            confidence=conf,
            device=self._detector_device,
        )
        self._fire_detectors[path] = det
        return det

    def any_fire_detector(self) -> Optional[FireSmokeDetector]:
        """First loaded fire detector (for /api/fire/status when any feed uses fire)."""
        if not self._fire_detectors:
            return None
        return next(iter(self._fire_detectors.values()))

    def merged_fire_alert_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for fd in self._fire_detectors.values():
            rows.extend(fd.get_alert_history(limit=limit))
        rows.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
        return rows[:limit]

    @staticmethod
    def _person_stream_dets(dets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Person / tracking stream only (excludes product overlay boxes)."""
        return [d for d in dets if (d or {}).get("det_stream", "person") == "person"]

    def add_camera(
        self,
        camera_id: int,
        source: str,
        stream_type: str = "rtsp",
        zone: str = "default",
        fps: int = 30,
        skip_frames: int = 1,
        roi: Optional[Dict] = None,
        model_path: Optional[str] = None,
        fire_model_path: Optional[str] = None,
        tracker_config: Optional[str] = None,
        enable_reid: bool = True,
        enable_fire: bool = False,
    ):
        """
        Register a camera for processing.
        
        Args:
            camera_id: Unique identifier
            source: RTSP URL, file path, or device index
            stream_type: "rtsp", "http", "file", or "webcam"
            zone: Which store zone this camera covers (e.g., "entrance", "aisle-1")
            fps: Camera FPS
            skip_frames: Process every Nth frame (higher = faster but less accurate)
            roi: Optional region of interest crop
            enable_reid: Run 512-d Torchreid embeddings + global gallery for this feed (GPU/CPU heavy).
            enable_fire: Run fire/smoke YOLO on this feed. Optional fire_model_path selects the .pt file (defaults to FIRE_MODEL_PATH).
            fire_model_path: Resolved absolute path to fire/smoke weights for this camera (person detection uses model_path separately).
        
        Example:
            # IP camera
            pipeline.add_camera(1, "rtsp://admin:pass@192.168.1.10:554/stream", zone="entrance")
            
            # Test with video file
            pipeline.add_camera(1, "test_videos/entrance.mp4", stream_type="file", zone="entrance")
            
            # Webcam
            pipeline.add_camera(1, "0", stream_type="webcam", zone="demo")
        """
        if self.memory_guard and getattr(self.memory_guard, "state", None):
            if getattr(self.memory_guard.state, "status", "") == "hard":
                raise RuntimeError("System under hard memory pressure; cannot add camera")
        if len(self._frame_counts) >= int(getattr(settings, "MAX_CAMERAS", 16)):
            raise RuntimeError(f"Max cameras reached ({getattr(settings, 'MAX_CAMERAS', 16)})")

        fps_target = max(1, min(int(fps), 240))
        skip_n = max(0, int(skip_frames))
        config = StreamConfig(
            camera_id=camera_id,
            source=source,
            stream_type=StreamType(stream_type),
            fps_target=fps_target,
            skip_frames=skip_n,
            decode_imgsz=int(getattr(settings, "DECODE_IMGSZ", 0) or 0),
            roi=roi,
        )
        self.stream_manager.add_camera(config)
        # If the pipeline is already running, kick off the capture thread for this new
        # camera; otherwise it would just sit idle and never produce frames (so multi-cam
        # added after Start Session never multi-streamed).
        if self.state == PipelineState.RUNNING:
            try:
                self.stream_manager.start_camera(camera_id)
            except Exception as e:
                logger.error(f"Failed to start newly added camera {camera_id}: {e}")
        self._frame_counts[camera_id] = 0
        self._camera_zones[camera_id] = zone
        self._camera_sources[int(camera_id)] = (str(source).strip()[:500] or "pipeline://unknown")
        self._camera_fps[int(camera_id)] = float(fps_target)
        self._camera_reid_enabled[int(camera_id)] = bool(enable_reid)
        self._camera_fire_enabled[int(camera_id)] = bool(enable_fire)
        if enable_fire:
            fp = fire_model_path or self._fire_model_path
            self._camera_fire_weights_path[int(camera_id)] = fp
            self._get_or_create_fire_detector(fp)
        else:
            self._camera_fire_weights_path.pop(int(camera_id), None)
        # Person/tracker YOLO (never use fire-only weights here — those belong in fire_model_path).
        effective_person_model = model_path or self._detector_model
        self._camera_models[camera_id] = effective_person_model
        # Create detector for this model if not exists
        self._get_or_create_detector(effective_person_model)
        
        # One tracker per camera so local track IDs are per-feed; Re-ID assigns global_id across cameras.
        # The pipeline tracks PERSONS only (class 0 in COCO) — cross-camera Re-ID,
        # crowd density, journeys etc. are all person-centric. Other models can
        # still be loaded via this tracker class outside the pipeline by passing
        # `classes=None` (track all) or a custom list.
        if camera_id not in self._trackers:
            self._trackers[camera_id] = MultiObjectTracker(
                model_path=effective_person_model,
                tracker_config=tracker_config or getattr(settings, "TRACKER_DEFAULT", "botsort.yaml"),
                classes=[0],
            )

        # Register zone in crowd density (one zone per camera).
        self.crowd.configure_zone(zone, camera_id=camera_id, max_capacity=50)

        logger.info(
            f"Camera {camera_id} added → zone: {zone} | capture_fps_cap={fps_target} "
            f"skip_frames={skip_n} reid={'on' if enable_reid else 'off'} "
            f"fire_smoke={'on' if enable_fire else 'off'}"
        )
        # Fire-and-forget: audit log this camera addition on the running loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._emit_lifecycle("camera_added", {
                    "camera_id": camera_id, "source": source,
                    "stream_type": stream_type, "zone": zone,
                }))
        except RuntimeError:
            pass

    def remove_camera(self, camera_id: int):
        """Remove a camera from processing."""
        self._stop_recording_impl(camera_id)
        self.stream_manager.remove_camera(camera_id)
        self._frame_counts.pop(camera_id, None)
        self._results_buffer.pop(camera_id, None)
        self._trackers.pop(camera_id, None)
        self._camera_models.pop(camera_id, None)  # Clean up model assignment
        self._camera_reid_enabled.pop(int(camera_id), None)
        self._camera_fire_enabled.pop(int(camera_id), None)
        self._camera_fire_weights_path.pop(int(camera_id), None)
        zone = self._camera_zones.pop(camera_id, None)
        self._camera_sources.pop(int(camera_id), None)
        self._camera_fps.pop(int(camera_id), None)
        self._last_global_by_track = {k: v for k, v in self._last_global_by_track.items() if k[0] != camera_id}
        self._last_product_overlay.pop(camera_id, None)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._emit_lifecycle("camera_removed", {
                    "camera_id": camera_id, "zone": zone,
                }))
        except RuntimeError:
            pass

    def set_broadcast(self, svc: Optional["BroadcastService"]) -> None:
        """Wire WebSocket broadcast for cross-camera Re-ID events."""
        self._broadcast = svc

    def _reid_on_for_camera(self, camera_id: int) -> bool:
        return bool(self._camera_reid_enabled.get(camera_id, True))

    @staticmethod
    def _crop_to_jpeg_base64(crop_bgr: np.ndarray, max_side: int = 160) -> str:
        """Encode a BGR crop as base64 JPEG (bounded size for WS payloads)."""
        if crop_bgr is None or crop_bgr.size == 0:
            return ""
        h, w = crop_bgr.shape[:2]
        m = max(h, w, 1)
        scale = min(1.0, float(max_side) / float(m))
        if scale < 1.0:
            crop_bgr = cv2.resize(
                crop_bgr,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )
        ok, buf = cv2.imencode(".jpg", crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            return ""
        return base64.b64encode(buf.tobytes()).decode("ascii")

    def _store_reid_snapshot(self, global_id: str, cam_id: int, crop_bgr: np.ndarray) -> str:
        """Remember last thumbnail per (global_id, camera) for cross-feed matching UI."""
        b64 = self._crop_to_jpeg_base64(crop_bgr)
        if not b64:
            return ""
        key = (global_id, int(cam_id))
        if key in self._reid_snapshots:
            self._reid_snapshots.move_to_end(key)
        self._reid_snapshots[key] = b64
        while len(self._reid_snapshots) > _MAX_REID_SNAPSHOT_CACHE:
            self._reid_snapshots.popitem(last=False)
        return b64

    async def _emit_reid_camera_transition(
        self,
        global_id: str,
        cam_id: int,
        prev_cam: int,
        current_thumb_b64: str,
        similarity: Optional[float],
    ) -> None:
        if not self._broadcast or prev_cam == cam_id:
            return
        prev_thumb = self._reid_snapshots.get((global_id, prev_cam), "")
        try:
            await self._broadcast.push_reid_match(
                global_id,
                cam_id,
                prev_cam,
                similarity=similarity,
                snapshot_previous=prev_thumb or None,
                snapshot_current=current_thumb_b64 or None,
            )
        except Exception as e:
            logger.debug(f"reid_match broadcast failed: {e}")

    @staticmethod
    def _even_video_dims(w: int, h: int) -> Tuple[int, int]:
        """MPEG-4 / most codecs expect even width and height."""
        w = max(2, int(w) - int(w) % 2)
        h = max(2, int(h) - int(h) % 2)
        return w, h

    def _init_recorder_writer(self, camera_id: int, frame_bgr: np.ndarray) -> None:
        """Open VideoWriter using the same size as frames we actually write (post-decode)."""
        rec = self._recorders.get(camera_id)
        if not rec:
            return
        writer, out_path = rec
        if writer is not None:
            return
        fh, fw = frame_bgr.shape[:2]
        w, h = self._even_video_dims(fw, fh)
        fps = max(1, int(self.processing_fps))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        new_writer = cv2.VideoWriter(str(out_path), fourcc, float(fps), (w, h))
        if not new_writer.isOpened():
            logger.error(f"VideoWriter could not open {out_path} at {w}x{h} fps={fps}")
            return
        self._recorders[camera_id] = (new_writer, out_path)
        self._recorder_target_wh[camera_id] = (w, h)
        logger.info(f"Recording writer opened: {out_path.name} {w}x{h} @ {fps} fps (mp4v)")

    def _write_recording_frame(self, camera_id: int, vis_bgr: np.ndarray) -> None:
        rec = self._recorders.get(camera_id)
        if not rec:
            return
        writer, _ = rec
        if writer is None:
            self._init_recorder_writer(camera_id, vis_bgr)
            rec = self._recorders.get(camera_id)
            if not rec:
                return
            writer, _ = rec
        if writer is None or not writer.isOpened():
            return
        tw, th = self._recorder_target_wh.get(camera_id, (vis_bgr.shape[1], vis_bgr.shape[0]))
        tw, th = self._even_video_dims(tw, th)
        if vis_bgr.shape[1] != tw or vis_bgr.shape[0] != th:
            vis_bgr = cv2.resize(vis_bgr, (tw, th), interpolation=cv2.INTER_AREA)
        writer.write(vis_bgr)

    def _stop_recording_impl(self, camera_id: int) -> Optional[str]:
        """Stop recording for a camera; return saved file path or None."""
        rec = self._recorders.pop(camera_id, None)
        self._recorder_target_wh.pop(camera_id, None)
        if not rec:
            return None
        writer, path = rec
        if writer is not None:
            try:
                writer.release()
            except Exception:
                pass
        # Flush detection log to JSON
        self._flush_detection_log(camera_id)
        try:
            p = Path(path)
            if p.is_file() and p.stat().st_size == 0:
                p.unlink(missing_ok=True)
                return None
        except OSError:
            pass
        if not Path(path).is_file():
            return None
        return str(path)

    def _flush_detection_log(self, camera_id: int):
        """Write accumulated detection log frames to a JSON file."""
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
                json.dump(log_data, f, indent=2, default=str)
            logger.info(f"Detection log saved: {log_path} ({log_data['total_frames']} frames)")
        except Exception as e:
            logger.error(f"Failed to save detection log: {e}")

    def start_recording(self, camera_id: int) -> Dict[str, Any]:
        """
        Start recording this camera's annotated feed to storage/footage.
        Requires the camera to be active and pipeline running.
        """
        if camera_id in self._recorders:
            return {"recording": True, "message": f"Camera {camera_id} already recording"}
        footage_dir = resolved_footage_dir()
        footage_dir.mkdir(parents=True, exist_ok=True)
        out_path = (footage_dir / f"camera_{camera_id}_{int(time.time())}.mp4").resolve()
        # Defer VideoWriter until first frame so (w,h) match decoded video (DECODE_IMGSZ).
        self._recorders[camera_id] = (None, out_path)
        # Start detection log with same name as video but .json in storage/logs
        log_path = self._logs_dir / f"{out_path.stem}.json"
        self._detection_logs[camera_id] = {
            "log_path": log_path,
            "frames": [],
            "video_file": str(out_path),
            "camera_id": camera_id,
            "model": self._camera_models.get(camera_id, self._detector_model),
            "start_time": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"Recording started for camera {camera_id} -> {out_path}")
        logger.info(f"Detection log started -> {log_path}")
        return {"recording": True, "path": str(out_path), "log_path": str(log_path), "camera_id": camera_id}

    def stop_recording(self, camera_id: int) -> Dict[str, Any]:
        """Stop recording and save the clip to footage storage."""
        # Capture log path before stopping (flush will pop it)
        log_info = self._detection_logs.get(camera_id)
        log_path = str(log_info["log_path"]) if log_info else None
        path = self._stop_recording_impl(camera_id)
        if path:
            logger.info(f"Recording stopped for camera {camera_id} -> {path}")
            return {"recording": False, "saved": path, "log_path": log_path, "camera_id": camera_id}
        return {"recording": False, "message": f"Camera {camera_id} was not recording"}

    def get_recording_status(self) -> Dict[str, Any]:
        """Return which cameras are currently recording."""
        return {
            "recording_cameras": list(self._recorders.keys()),
            "recording": list(self._recorders.keys()),
        }

    @staticmethod
    def _detection_to_dict(det: Any, track_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Normalize a detection (BBox dataclass or dict) into the dict shape the
        downstream analytics modules consume:
          {bbox: [x, y, w, h], confidence, class_name, track_id}
        """
        if isinstance(det, BBox):
            return {
                "bbox": [float(det.x), float(det.y), float(det.w), float(det.h)],
                "confidence": float(det.confidence),
                "class_name": det.class_name,
                "track_id": det.track_id if det.track_id is not None else track_id,
                "keypoints": det.keypoints,
            }
        bbox = det.get("bbox") or det.get("box") or []
        if len(bbox) < 4:
            return {"bbox": [0.0, 0.0, 0.0, 0.0], "confidence": 0.0, "class_name": "person", "track_id": track_id, "keypoints": None}
        return {
            "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
            "confidence": float(det.get("confidence", 0.0)),
            "class_name": str(det.get("class_name", "person")),
            "track_id": det.get("track_id", track_id),
            "keypoints": det.get("keypoints"),
        }

    @staticmethod
    def _iou(box1: List[float], box2: List[float]) -> float:
        """IoU for two boxes [x, y, w, h]. Returns 0 if no overlap."""
        x1, y1, w1, h1 = box1[0], box1[1], box1[2], box1[3]
        x2, y2, w2, h2 = box2[0], box2[1], box2[2], box2[3]
        ax1, ay1 = x1, y1
        ax2, ay2 = x1 + w1, y1 + h1
        bx1, by1 = x2, y2
        bx2, by2 = x2 + w2, y2 + h2
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        area_a = w1 * h1
        area_b = w2 * h2
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def _match_det_to_track(self, det_bbox: List[float], tracks: List[Any]) -> Optional[int]:
        """Return track_id of the track with best IoU to this detection, or None."""
        if not tracks:
            return None
        best_id, best_iou = None, 0.0
        for t in tracks:
            tid = getattr(t, "track_id", None)
            tbox = getattr(t, "bbox", None)
            if tid is None or tbox is None or len(tbox) < 4:
                continue
            iou = self._iou(det_bbox, tbox)
            if iou > best_iou:
                best_iou, best_id = iou, tid
        return best_id if best_iou >= 0.3 else None

    # ─────────────────────────────────────────────────────────────
    # CALLBACKS (for WebSocket push, DB persistence, etc.)
    # ─────────────────────────────────────────────────────────────

    def on_results(self, callback: Callable):
        """
        Register a callback that fires after each processing cycle.
        
        The callback receives:
          callback(results: Dict[int, CameraResult], global_state: GlobalState)
        
        Use this to:
          - Push results to WebSocket for live dashboard
          - Save detections to database
          - Trigger alerts (fire, overcrowding)
        """
        if callback in self._callbacks:
            return
        self._callbacks.append(callback)

    def set_event_bus(self, bus: EventBus) -> None:
        self.event_bus = bus or NullBus()

    def set_vector_store(self, store: Any) -> None:
        self.vector_store = store

    def set_memory_guard(self, guard: Any) -> None:
        self.memory_guard = guard

    def warm_reid_gallery(self, gallery_rows: List[Dict[str, Any]]) -> int:
        """
        Pre-load the global Re-ID gallery with rows from pgvector (survives restart).
        Each row: {"global_id": str, "vector": List[float]}.
        Returns the number of embeddings loaded.
        """
        loaded = 0
        for row in gallery_rows or []:
            try:
                gid = row.get("global_id")
                vec = row.get("vector")
                if not gid or not vec:
                    continue
                arr = np.asarray(vec, dtype=np.float32)
                norm = np.linalg.norm(arr)
                if norm > 0:
                    arr = arr / norm
                self.reid.add_to_gallery(gid, arr)
                loaded += 1
            except Exception:
                continue
        # Seed the global counter so new IDs don't collide with persisted ones
        max_suffix = 0
        for gid, _ in self.reid._gallery:
            try:
                if gid.startswith("PERSON-"):
                    n = int(gid.split("-")[-1])
                    if n > max_suffix:
                        max_suffix = n
            except Exception:
                continue
        if max_suffix >= self.global_state.total_persons_tracked:
            self.global_state.total_persons_tracked = max_suffix + 1
        logger.info(f"Re-ID gallery warmed: {loaded} embeddings, counter seeded at {self.global_state.total_persons_tracked}")
        return loaded

    def get_and_clear_pending_embeddings(self) -> List[Dict[str, Any]]:
        """
        Atomically drain the pending embeddings buffer.
        Called by PersistencePipelineCallback to ship Re-ID embeddings to pgvector.
        """
        with self._pending_embeddings_lock:
            drained = self._pending_embeddings
            self._pending_embeddings = []
        return drained

    def record_journey_leg(self, global_id: str, camera_id: int, zone: str, started_at: float) -> None:
        """Store the first-seen time for a (global_id, camera, zone) leg so persistence can compute dwell."""
        key = (global_id, camera_id, zone)
        self._journey_leg_start.setdefault(key, started_at)

    def on_lifecycle(self, hook: Callable[[str, Dict[str, Any]], Any]):
        """Subscribe to pipeline lifecycle events (start/stop/camera_added/camera_removed)."""
        self._lifecycle_hooks.append(hook)

    async def _emit_lifecycle(self, event: str, payload: Dict[str, Any]):
        for hook in self._lifecycle_hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(event, payload)
                else:
                    hook(event, payload)
            except Exception as e:
                logger.debug(f"Lifecycle hook failed for {event}: {e}")

    # ─────────────────────────────────────────────────────────────
    # MAIN PROCESSING LOOP
    # ─────────────────────────────────────────────────────────────

    async def start(self):
        """Start the synchronized multi-camera processing pipeline."""
        if self.state == PipelineState.RUNNING:
            logger.warning("Pipeline already running")
            return

        self.state = PipelineState.STARTING
        logger.info("🚀 Starting multi-camera pipeline...")

        # Start all camera streams
        started = self.stream_manager.start_all()
        logger.info(f"   Started {started} camera streams")

        # Wait for cameras to connect
        await asyncio.sleep(1.0)

        # Start main processing loop
        self.state = PipelineState.RUNNING
        self._processing_task = asyncio.create_task(self._processing_loop())
        await self._emit_lifecycle("pipeline_started", {
            "cameras": self.stream_manager.total_count,
            "target_fps": self.processing_fps,
        })
        logger.info("✅ Pipeline running — processing all cameras in sync")

    async def stop(self):
        """Stop the pipeline gracefully."""
        if self.state != PipelineState.RUNNING:
            return

        self.state = PipelineState.STOPPING
        logger.info("🛑 Stopping pipeline...")

        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        for cid in list(self._recorders.keys()):
            self._stop_recording_impl(cid)
        self.stream_manager.stop_all()
        self.state = PipelineState.IDLE
        await self._emit_lifecycle("pipeline_stopped", {
            "total_persons_tracked": self.global_state.total_persons_tracked,
            "total_detections": self._total_detections,
        })
        logger.info("Pipeline stopped")

    async def _processing_loop(self):
        """
        Single loop for ALL cameras — not independent. Each tick:
          1. Grab latest frame from EVERY camera
          2. Detection + tracking per camera
          3. GLOBAL Re-ID: one shared gallery; same person on Cam 1 and Cam 2 gets same global_id
          4. Analytics, Store Vibe, callbacks
        """
        interval = 1.0 / max(self.processing_fps, 1)

        while self.state == PipelineState.RUNNING:
            tick_start = time.time()
            tick_id = time.monotonic_ns()

            try:
                # Step 1: Grab frames from ALL cameras simultaneously
                frames = {}
                cam_ids = list(self._frame_counts.keys())
                # Slightly more time per tick when multiple feeds decode in parallel (file/RTSP).
                deadline_s = max(0.12, interval * (0.9 + 0.15 * max(0, len(cam_ids) - 1)))
                frame_reads = await asyncio.gather(
                    *[self.stream_manager.get_frame_async(cam_id, timeout=deadline_s) for cam_id in cam_ids],
                    return_exceptions=True,
                )
                for cam_id, result in zip(cam_ids, frame_reads):
                    if isinstance(result, Exception):
                        continue
                    if result:
                        frames[cam_id] = result  # (frame, timestamp)

                if not frames:
                    await asyncio.sleep(0.1)
                    continue

                # Step 2-6: Process all frames
                cycle_results = await self._process_synchronized_frames(frames, tick_id=tick_id)

                # Step 7: Fire callbacks
                for cb in self._callbacks:
                    try:
                        out = cb(cycle_results, self.global_state)
                        if inspect.isawaitable(out):
                            await out
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

                # Mirror key events onto pluggable event bus.
                for cam_id, r in cycle_results.items():
                    await self.event_bus.publish(
                        "detections",
                        key=f"camera:{cam_id}",
                        payload={
                            "tick_id": tick_id,
                            "camera_id": cam_id,
                            "person_count": len(self._person_stream_dets(r.detections or [])),
                            "active_tracks": len(r.tracks or []),
                            "ts": r.timestamp,
                        },
                    )
                    for alert in (r.fire_alerts or []):
                        await self.event_bus.publish(
                            "alerts",
                            key=f"camera:{cam_id}",
                            payload={"tick_id": tick_id, "camera_id": cam_id, **alert},
                        )
                if self.memory_guard and getattr(self.memory_guard.state, "status", "") in {"soft", "hard"}:
                    await self.event_bus.publish(
                        "alerts",
                        key="system",
                        payload={
                            "type": "system_pressure",
                            "status": self.memory_guard.state.status,
                            "tick_id": tick_id,
                            "memory": self.memory_guard.snapshot(),
                        },
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Pipeline processing error: {e}")
                self.global_state.last_updated = time.time()

            # Maintain target processing FPS
            elapsed = time.time() - tick_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _process_synchronized_frames(
        self, frames: Dict[int, tuple], tick_id: int = 0
    ) -> Dict[int, CameraResult]:
        """
        Process a synchronized batch of frames from all cameras.
        
        This is where the multi-camera magic happens:
          - Detection & tracking are per-camera
          - Re-ID gallery is GLOBAL (shared across all cameras)
          - Analytics modules can see all cameras at once
        """
        results = {}
        all_detections = {}
        all_tracks = {}

        process_start = time.time()

        # ── PHASE 1: Detection + Tracking (per-camera, can run in parallel) ──
        for cam_id, (frame, timestamp) in frames.items():
            self._frame_counts[cam_id] = self._frame_counts.get(cam_id, 0) + 1
            frame_num = self._frame_counts[cam_id]

            # Get the correct detector for this camera (supports per-camera model selection)
            camera_model = self._camera_models.get(cam_id, self._detector_model)
            detector = self._get_or_create_detector(camera_model)
            
            # Detect persons (primary YOLO; class 0 only)
            raw_person = await asyncio.to_thread(
                detector.detect, frame
            )

            # Track persons (per-camera ByteTrack/BoT-SORT: local IDs for this feed only)
            tracker = self._trackers.get(cam_id)
            if tracker is None:
                tracker = MultiObjectTracker(model_path=camera_model, classes=[0])
                self._trackers[cam_id] = tracker
            tracks = await asyncio.to_thread(tracker.update, frame)
            all_tracks[cam_id] = tracks

            # Normalize person detections → dicts; attach track_id via IoU match against tracks.
            norm_dets: List[Dict[str, Any]] = []
            for raw in raw_person:
                if isinstance(raw, BBox):
                    bbox = [float(raw.x), float(raw.y), float(raw.w), float(raw.h)]
                else:
                    bbox = raw.get("bbox") or raw.get("box") or []
                tid = self._match_det_to_track(bbox, tracks) if len(bbox) >= 4 else None
                d = self._detection_to_dict(raw, track_id=tid)
                d["det_stream"] = "person"
                norm_dets.append(d)

            # Optional product YOLO: throttled + cached overlay (no tracker / Re-ID / gallery load)
            prod_detector = self._get_product_detector()
            interval = max(1, int(getattr(settings, "PRODUCT_DETECT_EVERY_N_FRAMES", 3)))
            max_prod = max(1, int(getattr(settings, "PRODUCT_MAX_DETECTIONS_PER_FRAME", 25)))
            if prod_detector is not None:
                if (frame_num - 1) % interval == 0:
                    raw_prod = await asyncio.to_thread(prod_detector.detect, frame)
                    raw_prod = sorted(raw_prod, key=lambda b: -b.confidence)[:max_prod]
                    product_dicts: List[Dict[str, Any]] = []
                    for raw in raw_prod:
                        pd = self._detection_to_dict(raw, track_id=None)
                        pd["det_stream"] = "product"
                        pd["global_id"] = None
                        product_dicts.append(pd)
                    self._last_product_overlay[cam_id] = product_dicts
                else:
                    product_dicts = list(self._last_product_overlay.get(cam_id) or [])
                merged = norm_dets + product_dicts
            else:
                merged = norm_dets

            all_detections[cam_id] = merged
            self._total_detections += len(norm_dets)

            results[cam_id] = CameraResult(
                camera_id=cam_id,
                timestamp=timestamp,
                frame_number=frame_num,
                detections=merged,
                tracks=tracks,
                tick_id=tick_id,
            )

        # ── PHASE 2: Cross-Camera Re-ID (GLOBAL gallery, optional per camera) ──
        # Body-based matching (512-d OSNet). Skipped entirely for feeds with enable_reid=False.
        new_embeddings: List[Dict[str, Any]] = []
        reid_any = any(self._reid_on_for_camera(cid) for cid in frames.keys())
        if not reid_any:
            for cam_id in frames:
                for det in all_detections.get(cam_id, []):
                    det["global_id"] = None
        else:
            async with self._reid_merge_lock:
                for cam_id, (frame, timestamp) in frames.items():
                    if not self._reid_on_for_camera(cam_id):
                        for det in all_detections.get(cam_id, []):
                            det["global_id"] = None
                        continue
                    for det in all_detections.get(cam_id, []):
                        try:
                            if (det or {}).get("det_stream") == "product":
                                det["global_id"] = None
                                continue
                            bbox = det.get("bbox") or []
                            if len(bbox) < 4:
                                continue
                            x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                            x, y = max(0, x), max(0, y)
                            crop = frame[y : y + h, x : x + w]
                            if crop.size == 0:
                                continue
                            track_id = det.get("track_id")
                            embedding = await asyncio.to_thread(
                                self.reid.extract_embedding, crop
                            )
                            if embedding is None:
                                continue
                            key = (cam_id, track_id) if track_id is not None else None
                            prev_global = self._last_global_by_track.get(key) if key else None
                            matches = self.reid.search_gallery(embedding, top_k=1)
                            match_sim: Optional[float] = None
                            if matches:
                                global_id = matches[0]["id"]
                                match_sim = float(matches[0].get("similarity", 0.0))
                                if match_sim < 0.92:
                                    self.reid.add_embedding_to_id(global_id, embedding)
                            elif prev_global and key:
                                global_id = prev_global
                                match_sim = None
                            else:
                                global_id = f"PERSON-{self.global_state.total_persons_tracked:05d}"
                                self.reid.add_to_gallery(global_id, embedding)
                                self.global_state.total_persons_tracked += 1
                                match_sim = None
                            if key:
                                self._last_global_by_track[key] = global_id
                            det["global_id"] = global_id
                            thumb_b64 = self._store_reid_snapshot(global_id, cam_id, crop)
                            prev_cam_seen = self._global_id_last_camera.get(global_id)
                            if (
                                prev_cam_seen is not None
                                and int(prev_cam_seen) != int(cam_id)
                                and self._broadcast
                            ):
                                now_ts = time.time()
                                if (
                                    now_ts - self._last_reid_ws_emit.get(global_id, 0.0)
                                    >= _REID_WS_EMIT_MIN_INTERVAL_S
                                ):
                                    await self._emit_reid_camera_transition(
                                        global_id,
                                        int(cam_id),
                                        int(prev_cam_seen),
                                        thumb_b64,
                                        match_sim if matches else None,
                                    )
                                    self._last_reid_ws_emit[global_id] = now_ts
                            self._global_id_last_camera[global_id] = int(cam_id)
                            results[cam_id].reid_matches.append({
                                "global_id": global_id,
                                "camera_id": cam_id,
                                "track_id": track_id,
                                "bbox": bbox,
                                "timestamp": timestamp,
                            })
                            new_embeddings.append({
                                "camera_id": cam_id,
                                "track_id": track_id,
                                "global_id": global_id,
                                "embedding": [float(v) for v in embedding.tolist()],
                                "confidence": float(det.get("confidence", 0.0)),
                                "bbox": bbox,
                                "timestamp": timestamp,
                            })
                        except Exception as e:
                            logger.debug(f"Re-ID error cam {cam_id}: {e}")
        if new_embeddings:
            with self._pending_embeddings_lock:
                self._pending_embeddings.extend(new_embeddings)

        # ── PHASE 3: Analytics (parallel across modules) ──
        for cam_id, (frame, timestamp) in frames.items():
            dets = self._person_stream_dets(all_detections.get(cam_id, []))
            zone = self._camera_zones.get(cam_id, "default")

            # Crowd density (always on; cheap)
            try:
                crowd_status = self.crowd.update(cam_id, dets)
                results[cam_id].crowd_status = crowd_status
            except Exception as e:
                logger.debug(f"Crowd update error cam {cam_id}: {e}")

            # Fire/smoke — separate YOLO; per-feed weights in _fire_detectors.
            if self._camera_fire_enabled.get(int(cam_id), False):
                fp = self._camera_fire_weights_path.get(int(cam_id)) or self._fire_model_path
                fd = self._fire_detectors.get(fp) if fp else None
                if fd is not None:
                    try:
                        fire_results = await asyncio.to_thread(
                            fd.detect, frame, cam_id, zone
                        )
                        results[cam_id].fire_alerts = fire_results
                        if fire_results:
                            self.global_state.fire_alert_active = True
                            logger.warning(f"FIRE/SMOKE ALERT on Camera {cam_id}!")
                    except Exception as e:
                        logger.debug(f"Fire detect error cam {cam_id}: {e}")

            # Emotion recognition — returns a single summary dict per frame.
            if self._enable_emotions and self.emotion:
                try:
                    summary = await asyncio.to_thread(
                        self.emotion.analyze_frame_summary, frame, cam_id, zone
                    )
                    results[cam_id].emotions = summary
                except Exception as e:
                    logger.debug(f"Emotion analyze error cam {cam_id}: {e}")

            # Shelf analytics (tracks = normalized detections with track_id).
            if self._enable_shelf and self.shelf_tracker:
                try:
                    shelf_data = self.shelf_tracker.update(cam_id, dets, timestamp)
                    results[cam_id].shelf_data = shelf_data
                except Exception as e:
                    logger.debug(f"Shelf update error cam {cam_id}: {e}")

            # Checkout analytics
            if self._enable_checkout and self.checkout:
                try:
                    checkout_data = self.checkout.update(cam_id, dets, timestamp)
                    results[cam_id].checkout_data = checkout_data
                except Exception as e:
                    logger.debug(f"Checkout update error cam {cam_id}: {e}")

        # ── PHASE 4: Store Vibe Score (aggregate everything) ──
        try:
            total_people = sum(len(self._person_stream_dets(d)) for d in all_detections.values())
            sentiment_samples = 0
            avg_sentiment = 0.0
            engagement_accum = 0.0
            engagement_samples = 0

            for r in results.values():
                if r.emotions and isinstance(r.emotions, dict):
                    samples = r.emotions.get("sample_count", 0)
                    sent = r.emotions.get("sentiment_score", 0.0)
                    if samples:
                        avg_sentiment += sent
                        sentiment_samples += 1
                if r.shelf_data and isinstance(r.shelf_data, dict):
                    eng = r.shelf_data.get("engagement_score", 0)
                    if eng:
                        engagement_accum += eng
                        engagement_samples += 1

            if sentiment_samples > 0:
                avg_sentiment /= sentiment_samples
            avg_engagement = engagement_accum / max(engagement_samples, 1)

            vibe = self.vibe_engine.calculate(
                sentiment_score=avg_sentiment,
                crowd_count=total_people,
                max_capacity=self._total_capacity(),
                engagement_score=avg_engagement,
                foot_traffic=total_people,
            )
            self.global_state.vibe_score = vibe.get("overall_score", 0)
            self._latest_vibe = vibe
            await self.event_bus.publish(
                "vibe",
                key="store",
                payload={
                    "overall_score": self.global_state.vibe_score,
                    "label": vibe.get("vibe_label", "Steady"),
                    "breakdown": dict(self.global_state.zone_occupancy),
                    "tick_id": tick_id,
                },
            )
        except Exception as e:
            logger.debug(f"Vibe calc error: {e}")

        # ── PHASE 5: Update global state ──
        self.global_state.last_updated = time.time()

        # Zone occupancy from all cameras
        for cam_id, result in results.items():
            if result.crowd_status:
                zone = result.crowd_status.get("zone", f"cam-{cam_id}")
                count = result.crowd_status.get(
                    "person_count",
                    len(self._person_stream_dets(result.detections or [])),
                )
                self.global_state.zone_occupancy[zone] = count

        # Active global IDs this tick only (avoids stale rows when Re-ID is off or people leave FOV)
        self.global_state.active_tracks.clear()
        for cam_id, result in results.items():
            for match in result.reid_matches:
                self.global_state.active_tracks[match["global_id"]] = {
                    "camera_id": cam_id,
                    "last_seen": result.timestamp,
                    "bbox": match.get("bbox"),
                }

        # Calculate processing time
        total_ms = (time.time() - process_start) * 1000
        for r in results.values():
            r.processing_time_ms = total_ms / len(results)

        # Draw detections on frames and store JPEGs for live stream (dashboard/CCTV)
        for cam_id, (frame, _) in frames.items():
            r = results.get(cam_id)
            if r and frame is not None:
                try:
                    vis = self._draw_annotations(frame.copy(), r)
                    _, jpeg = cv2.imencode(".jpg", vis)
                    with self._jpeg_lock:
                        self._latest_annotated_jpeg[cam_id] = jpeg.tobytes()
                    # Write to recorder if this camera is recording
                    if cam_id in self._recorders:
                        self._write_recording_frame(cam_id, vis)
                    # Append detection log entry for this frame
                    log_entry = self._detection_logs.get(cam_id)
                    if log_entry is not None:
                        frame_log = {
                            "frame_number": r.frame_number,
                            "timestamp": r.timestamp,
                            "detections": [],
                        }
                        for det in r.detections:
                            d = det if isinstance(det, dict) else self._detection_to_dict(det)
                            reid_match = next(
                                (m for m in r.reid_matches if m.get("track_id") == d.get("track_id")),
                                None
                            )
                            frame_log["detections"].append({
                                "track_id": d.get("track_id"),
                                "class_name": d.get("class_name", "unknown"),
                                "confidence": round(d.get("confidence", 0.0), 4),
                                "bbox": d.get("bbox", []),
                                "global_id": reid_match.get("global_id") if reid_match else None,
                                "region": self._camera_zones.get(cam_id, "global"),
                                "det_stream": d.get("det_stream", "person"),
                            })
                        frame_log["detection_count"] = len(frame_log["detections"])
                        log_entry["frames"].append(frame_log)
                except Exception as e:
                    logger.debug(f"Annotation draw error cam {cam_id}: {e}")

        # Store in buffer for API access
        self._results_buffer = results

        return results

    # COCO-17 keypoint skeleton edges (Ultralytics pose models)
    _POSE_SKELETON = [
        (5, 7), (7, 9), (6, 8), (8, 10),         # arms
        (11, 13), (13, 15), (12, 14), (14, 16),  # legs
        (5, 6), (11, 12), (5, 11), (6, 12),      # torso
        (0, 1), (0, 2), (1, 3), (2, 4),          # face
        (0, 5), (0, 6),                           # neck-shoulders
    ]
    # Colors per joint group (BGR): face=yellow, arms=cyan, legs=magenta, torso=green
    _POSE_KP_COLORS = {
        **{i: (0, 255, 255) for i in range(0, 5)},      # face: nose, eyes, ears
        **{i: (255, 200, 0) for i in [5, 6, 7, 8, 9, 10]},  # arms
        **{i: (255, 0, 255) for i in [11, 12, 13, 14, 15, 16]},  # legs
    }

    @staticmethod
    def _color_for_track(track_id: Optional[int]) -> Tuple[int, int, int]:
        """Deterministic vivid color (BGR) for a track_id. Same ID -> same color across frames."""
        if track_id is None:
            return (0, 255, 0)  # green for untracked
        # Hash to HSV hue, full saturation/value for visibility
        hue = (int(track_id) * 47) % 180  # OpenCV hue range: 0-179
        hsv = np.uint8([[[hue, 220, 255]]])
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
        return (int(bgr[0]), int(bgr[1]), int(bgr[2]))

    def _draw_pose_skeleton(self, frame: np.ndarray, keypoints: List[List[float]], color: Tuple[int, int, int]):
        """Draw COCO-17 pose skeleton on frame. keypoints: list of [x, y, conf]."""
        if not keypoints or len(keypoints) < 17:
            return
        kp_thresh = 0.3
        # Draw bones
        for a, b in self._POSE_SKELETON:
            if a >= len(keypoints) or b >= len(keypoints):
                continue
            ka, kb = keypoints[a], keypoints[b]
            if ka[2] < kp_thresh or kb[2] < kp_thresh:
                continue
            pa = (int(ka[0]), int(ka[1]))
            pb = (int(kb[0]), int(kb[1]))
            cv2.line(frame, pa, pb, color, 2, cv2.LINE_AA)
        # Draw joints
        for i, kp in enumerate(keypoints):
            if kp[2] < kp_thresh:
                continue
            cx, cy = int(kp[0]), int(kp[1])
            joint_color = self._POSE_KP_COLORS.get(i, color)
            cv2.circle(frame, (cx, cy), 3, joint_color, -1, cv2.LINE_AA)
            cv2.circle(frame, (cx, cy), 4, (255, 255, 255), 1, cv2.LINE_AA)

    def _draw_annotations(self, frame: np.ndarray, result: "CameraResult") -> np.ndarray:
        """Draw detection bboxes, per-track colors, labels with class name + track_id, and pose skeleton."""
        h, w = frame.shape[:2]

        # Shelf ROIs (same pixel space as detector output). Clamped so OpenCV never sees inverted/huge rects.
        if self._enable_shelf and self.shelf_tracker and result.camera_id is not None:
            try:
                for z in self.shelf_tracker.zones:
                    if z.camera_id != result.camera_id:
                        continue
                    zx1, zy1, zx2, zy2 = z.bbox
                    zx1 = int(max(0, min(zx1, w - 1)))
                    zx2 = int(max(0, min(zx2, w - 1)))
                    zy1 = int(max(0, min(zy1, h - 1)))
                    zy2 = int(max(0, min(zy2, h - 1)))
                    if zx2 <= zx1 or zy2 <= zy1:
                        continue
                    cv2.rectangle(
                        frame, (zx1, zy1), (zx2, zy2),
                        (200, 180, 100), 2, cv2.LINE_AA,
                    )
                    label_z = str(z.zone_name)[:48]
                    cv2.putText(
                        frame, label_z, (zx1, max(16, zy1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 210, 170), 1, cv2.LINE_AA,
                    )
            except Exception as e:
                logger.debug(f"Shelf ROI overlay error: {e}")

        # Build a quick lookup: track_id -> global_id for label suffix
        gid_by_tid = {m.get("track_id"): m.get("global_id") for m in result.reid_matches if m.get("track_id") is not None}

        for det in result.detections:
            is_product = False
            if hasattr(det, "x"):
                x, y, bw, bh = int(det.x), int(det.y), int(det.w), int(det.h)
                conf = float(getattr(det, "confidence", 0))
                track_id = getattr(det, "track_id", None)
                class_name = getattr(det, "class_name", "object")
                keypoints = getattr(det, "keypoints", None)
            else:
                bbox = det.get("bbox", det.get("box", []))
                if len(bbox) < 4:
                    continue
                x, y, bw, bh = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                conf = float(det.get("confidence", 0))
                track_id = det.get("track_id")
                class_name = det.get("class_name", "object")
                keypoints = det.get("keypoints")
                is_product = det.get("det_stream") == "product"

            x, y = max(0, x), max(0, y)
            if bw < 1 or bh < 1:
                continue
            bw = min(int(bw), max(1, w - x))
            bh = min(int(bh), max(1, h - y))
            if x >= w or y >= h:
                continue
            color = (0, 140, 255) if is_product else self._color_for_track(track_id)
            box_thick = 1 if is_product else 2

            # Label: product boxes have no track id / Re-ID
            tid_str = "" if is_product else (f"#{track_id} " if track_id is not None else "")
            label = f"{class_name} {tid_str}{conf:.0%}".strip()

            # Filled label background for readability
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.5 if is_product else 0.55
            thickness = 1
            (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            label_y = max(th + 6, y)
            label_bg_top = label_y - th - 6
            label_bg_bottom = label_y
            cv2.rectangle(
                frame,
                (x, y),
                (x + bw, y + bh),
                color,
                box_thick,
                cv2.LINE_AA,
            )
            cv2.rectangle(
                frame,
                (x, label_bg_top),
                (x + tw + 8, label_bg_bottom),
                color, -1
            )
            cv2.putText(
                frame, label, (x + 4, label_y - 4),
                font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA
            )

            # Pose skeleton if available (person stream only)
            if keypoints and not is_product:
                self._draw_pose_skeleton(frame, keypoints, color)

            # Global ID below bbox (from Re-ID) — persons only
            if not is_product:
                gid = gid_by_tid.get(track_id) if track_id is not None else None
                if gid:
                    cv2.putText(
                        frame, str(gid), (x, min(h - 5, y + bh + 18)),
                        font, 0.45, (255, 200, 0), 1, cv2.LINE_AA
                    )

        if result.fire_alerts:
            cv2.putText(
                frame, "FIRE ALERT", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA
            )
        return frame

    def get_latest_annotated_jpeg(self, camera_id: int) -> Optional[bytes]:
        """Get latest annotated frame as JPEG bytes for MJPEG stream."""
        with self._jpeg_lock:
            return self._latest_annotated_jpeg.get(camera_id)

    # ─────────────────────────────────────────────────────────────
    # STATUS & METRICS
    # ─────────────────────────────────────────────────────────────

    def _reid_status_payload(self) -> Dict[str, Any]:
        """Structured Re-ID stats for dashboard / Cross-feed page (512-d gallery)."""
        gallery = getattr(self.reid, "_gallery", []) or []
        unique_ids = len({g for g, _ in gallery})
        cam_keys = list(self._frame_counts.keys())
        any_on = any(self._reid_on_for_camera(cid) for cid in cam_keys) if cam_keys else False
        return {
            "backend_status": "loaded" if getattr(self.reid, "model", None) else "mock",
            "gallery_size": len(gallery),
            "unique_identities": unique_ids,
            "threshold": float(getattr(self.reid, "similarity_threshold", 0.6)),
            "model": str(getattr(self.reid, "model_name", "osnet_x1_0")),
            "embedding_dim": int(getattr(self.reid, "EMBEDDING_DIM", 512)),
            "any_camera_enabled": any_on,
            "per_camera_enabled": {str(int(k)): self._reid_on_for_camera(k) for k in cam_keys},
        }

    def get_status(self) -> Dict[str, Any]:
        """Get complete pipeline status for dashboard."""
        stream_stats = self.stream_manager.get_all_stats()
        return {
            "state": self.state.value,
            "cameras": {
                "total": self.stream_manager.total_count,
                "active": self.stream_manager.active_count,
                "stats": {
                    cam_id: {
                        "connected": s.is_connected,
                        "fps": s.fps_actual,
                        "frames_read": s.frames_read,
                        "frames_dropped": s.frames_dropped,
                        "resolution": s.resolution,
                        "uptime": round(s.uptime_seconds, 1),
                        "reconnects": s.reconnect_count,
                    }
                    for cam_id, s in stream_stats.items()
                },
            },
            "ai_modules": {
                "detector": "loaded" if self.detector.model else "mock",
                "tracker": "loaded" if (self._trackers and next(iter(self._trackers.values())).model) else "fallback" if self._trackers else "no cameras",
                "reid": self._reid_status_payload(),
                "reid_gallery_size": len(getattr(self.reid, "_gallery", []) or []),
                "emotion": "enabled" if self._enable_emotions else "disabled",
                "fire": (
                    "armed"
                    if self._fire_detectors and any(self._camera_fire_enabled.values())
                    else "off"
                ),
                "shelf": "enabled" if self._enable_shelf else "disabled",
                "checkout": "enabled" if self._enable_checkout else "disabled",
                "product_yolo": {
                    "enabled": bool((getattr(settings, "PRODUCT_YOLO_PATH", None) or "").strip()),
                    "path": (getattr(settings, "PRODUCT_YOLO_PATH", None) or "") or None,
                    "every_n_frames": int(getattr(settings, "PRODUCT_DETECT_EVERY_N_FRAMES", 3)),
                    "max_boxes": int(getattr(settings, "PRODUCT_MAX_DETECTIONS_PER_FRAME", 25)),
                },
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
                "frame_counts": self._frame_counts,
            },
            "runtime": {
                "profile": getattr(settings, "RUNTIME_PROFILE", "laptop"),
                "event_bus_backend": getattr(settings, "EVENT_BUS_BACKEND", "redis"),
                "vector_store_backend": getattr(settings, "VECTOR_STORE_BACKEND", "pgvector"),
                "memory": self.memory_guard.snapshot() if self.memory_guard else {"status": "unknown"},
            },
        }

    def get_latest_results(self, camera_id: Optional[int] = None) -> Any:
        """Get latest processing results."""
        if camera_id:
            return self._results_buffer.get(camera_id)
        return self._results_buffer

    def _total_capacity(self) -> int:
        """Sum of per-zone max_capacity for all registered zones (>=1)."""
        if not getattr(self.crowd, "zones", None):
            return 200
        total = sum(getattr(z, "max_capacity", 50) for z in self.crowd.zones)
        return max(total, 1)

    # ─────────────────────────────────────────────────────────────
    # ANALYTICS SNAPSHOTS (for REST routers)
    # ─────────────────────────────────────────────────────────────

    def get_analytics_snapshot(self) -> Dict[str, Any]:
        """
        Unified snapshot used by the analytics routers and dashboard overview.
        Built purely from the latest per-camera CameraResult buffer so it's
        O(cameras) and doesn't re-run any AI.
        """
        crowd_zones: List[Dict[str, Any]] = []
        fire_alerts: List[Dict[str, Any]] = []
        emotions_per_zone: List[Dict[str, Any]] = []
        shelf_per_camera: List[Dict[str, Any]] = []
        checkout_lanes: List[Dict[str, Any]] = []
        total_detections_snapshot = 0

        for cam_id, r in self._results_buffer.items():
            total_detections_snapshot += len(self._person_stream_dets(r.detections or []))
            if r.crowd_status:
                crowd_zones.append(r.crowd_status)
            if r.fire_alerts:
                fire_alerts.extend(r.fire_alerts)
            if r.emotions and isinstance(r.emotions, dict) and r.emotions.get("sample_count"):
                emotions_per_zone.append(r.emotions)
            if r.shelf_data and isinstance(r.shelf_data, dict):
                shelf_per_camera.append(r.shelf_data)
            if r.checkout_data and isinstance(r.checkout_data, dict):
                lanes = r.checkout_data.get("lanes") or []
                checkout_lanes.extend(lanes)

        # Sentiment aggregate across all zones
        total_sentiment_samples = sum(e.get("sample_count", 0) for e in emotions_per_zone)
        weighted_sentiment = sum(
            e.get("sentiment_score", 0) * e.get("sample_count", 0) for e in emotions_per_zone
        )
        overall_sentiment = (
            weighted_sentiment / total_sentiment_samples if total_sentiment_samples else 0.0
        )

        # Shelf rankings pulled live from the analytics module
        shelf_rankings = (
            self.shelf_tracker.get_zone_rankings() if self.shelf_tracker else []
        )

        # Fire alert history — last 50 (merged across loaded fire checkpoints)
        fire_history = self.merged_fire_alert_history(limit=50)

        current_vibe = self._latest_vibe or self.vibe_engine.get_current() or {
            "overall_score": 0.0,
            "sentiment_score": 0.0,
            "energy_score": 0.0,
            "engagement_score": 0.0,
            "foot_traffic_score": 0.0,
            "vibe_label": "Quiet",
        }

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cameras": {
                "total": self.stream_manager.total_count,
                "active": self.stream_manager.active_count,
                "zones": dict(self._camera_zones),
            },
            "detections": {
                "active_frame": total_detections_snapshot,
                "total_processed": self._total_detections,
                "frames_processed": int(sum(self._frame_counts.values())),
                "frame_counts_by_camera": dict(self._frame_counts),
            },
            "crowd": {
                "zones": crowd_zones,
                "total_occupancy": sum(z.get("person_count", 0) for z in crowd_zones),
                "critical_zones": [
                    z for z in crowd_zones if z.get("classification") == "critical"
                ],
            },
            "fire": {
                "active_alerts": fire_alerts,
                "history": fire_history,
                "active": bool(fire_alerts),
            },
            "emotions": {
                "per_zone": emotions_per_zone,
                "overall_sentiment": round(overall_sentiment, 3),
                "samples": total_sentiment_samples,
            },
            "shelf": {
                "rankings": shelf_rankings,
                "per_camera": shelf_per_camera,
            },
            "checkout": {
                "lanes": checkout_lanes,
                "summary": self.checkout.get_summary() if self.checkout else {},
            },
            "vibe": current_vibe,
            "reid": {
                "total_persons_tracked": self.global_state.total_persons_tracked,
                "active_tracks": len(self.global_state.active_tracks),
            },
        }
