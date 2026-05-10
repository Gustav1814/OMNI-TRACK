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
import time
import json
import numpy as np
import cv2
from typing import Dict, List, Optional, Any, Callable, Tuple
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
from app.config import settings
from pathlib import Path
from app.services.event_bus import EventBus, NullBus


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
        fire_model: str = "yolov8n.pt",
        device: str = "auto",
        confidence: float = 0.5,
        processing_fps: int = 15,     # How many frames/sec to process per camera
        reid_threshold: float = 0.6,
        reid_embeddings_per_id: int = 5,
        enable_emotions: bool = True,
        enable_fire: bool = True,
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
        self._enable_fire = enable_fire
        self._enable_shelf = enable_shelf
        self._enable_checkout = enable_checkout

        self.emotion = EmotionRecognizer() if enable_emotions else None
        self.fire_detector = FireSmokeDetector(model_path=fire_model) if enable_fire else None
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
        # Per-camera recording: camera_id -> (VideoWriter, output_path)
        self._recorders: Dict[int, tuple] = {}
        # Per-camera detection logs: camera_id -> {"log_path": Path, "frames": []}
        self._detection_logs: Dict[int, Dict[str, Any]] = {}
        # Ensure logs directory exists
        self._logs_dir = Path("storage/logs")
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        # camera_id -> zone_name (set at add_camera, used by analytics snapshot)
        self._camera_zones: Dict[int, str] = {}
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

        # Lifecycle hooks: external observers (e.g. audit log writer) register
        # callables that fire on pipeline events. Hooks are async-friendly.
        self._lifecycle_hooks: List[Callable[[str, Dict[str, Any]], Any]] = []

        logger.info(f"Pipeline initialized | device={device} | modules: "
                     f"emotion={enable_emotions} fire={enable_fire} "
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
            )
        return self._detectors[model_path]

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
        tracker_config: Optional[str] = None,
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
        self._frame_counts[camera_id] = 0
        self._camera_zones[camera_id] = zone
        # Store model assignment for this camera (default if not specified)
        effective_model = model_path or self._detector_model
        self._camera_models[camera_id] = effective_model
        # Create detector for this model if not exists
        self._get_or_create_detector(effective_model)
        
        # One tracker per camera so local track IDs are per-feed; Re-ID assigns global_id across cameras.
        if camera_id not in self._trackers:
            self._trackers[camera_id] = MultiObjectTracker(
                model_path=effective_model,
                tracker_config=tracker_config or getattr(settings, "TRACKER_DEFAULT", "botsort.yaml"),
            )

        # Register zone in crowd density (one zone per camera).
        self.crowd.configure_zone(zone, camera_id=camera_id, max_capacity=50)

        logger.info(
            f"Camera {camera_id} added → zone: {zone} | capture_fps_cap={fps_target} skip_frames={skip_n}"
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
        zone = self._camera_zones.pop(camera_id, None)
        self._last_global_by_track = {k: v for k, v in self._last_global_by_track.items() if k[0] != camera_id}
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._emit_lifecycle("camera_removed", {
                    "camera_id": camera_id, "zone": zone,
                }))
        except RuntimeError:
            pass

    def _stop_recording_impl(self, camera_id: int) -> Optional[str]:
        """Stop recording for a camera; return saved file path or None."""
        rec = self._recorders.pop(camera_id, None)
        if not rec:
            return None
        writer, path = rec
        try:
            writer.release()
        except Exception:
            pass
        # Flush detection log to JSON
        self._flush_detection_log(camera_id)
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
        stats = self.stream_manager.get_stats(camera_id)
        w, h = (stats.resolution if stats and stats.resolution[0] else (1280, 720))[:2]
        if w <= 0 or h <= 0:
            w, h = 1280, 720
        footage_dir = Path(settings.FOOTAGE_DIR)
        footage_dir.mkdir(parents=True, exist_ok=True)
        out_path = footage_dir / f"camera_{camera_id}_{int(time.time())}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_path), fourcc, max(1, self.processing_fps), (int(w), int(h)))
        if not writer.isOpened():
            logger.error(f"Failed to create recorder for camera {camera_id}")
            return {"recording": False, "error": "Failed to create video file"}
        self._recorders[camera_id] = (writer, out_path)
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
                deadline_s = max(0.05, interval * 0.6)
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
                        if asyncio.iscoroutinefunction(cb):
                            await cb(cycle_results, self.global_state)
                        else:
                            cb(cycle_results, self.global_state)
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
                            "person_count": len(r.detections or []),
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
            
            # Detect persons
            raw_detections = await asyncio.to_thread(
                detector.detect, frame
            )

            # Track persons (per-camera ByteTrack: local IDs for this feed only)
            tracker = self._trackers.get(cam_id)
            if tracker is None:
                tracker = MultiObjectTracker(model_path=camera_model)
                self._trackers[cam_id] = tracker
            tracks = await asyncio.to_thread(tracker.update, frame)
            all_tracks[cam_id] = tracks

            # Normalize detections → dicts; attach track_id via IoU match against tracks.
            norm_dets: List[Dict[str, Any]] = []
            for raw in raw_detections:
                if isinstance(raw, BBox):
                    bbox = [float(raw.x), float(raw.y), float(raw.w), float(raw.h)]
                else:
                    bbox = raw.get("bbox") or raw.get("box") or []
                tid = self._match_det_to_track(bbox, tracks) if len(bbox) >= 4 else None
                norm_dets.append(self._detection_to_dict(raw, track_id=tid))
            all_detections[cam_id] = norm_dets
            self._total_detections += len(norm_dets)

            results[cam_id] = CameraResult(
                camera_id=cam_id,
                timestamp=timestamp,
                frame_number=frame_num,
                detections=norm_dets,
                tracks=tracks,
                tick_id=tick_id,
            )

        # ── PHASE 2: Cross-Camera Re-ID (GLOBAL gallery) ──
        # Body-based matching (works when face not visible). Multi-view: we store several
        # embeddings per identity and use best similarity. Temporal consistency: same
        # (camera, track) keeps same global_id to avoid flicker when pose changes.
        # Each produced embedding is queued for pgvector persistence (drained by the
        # PersistencePipelineCallback each tick).
        new_embeddings: List[Dict[str, Any]] = []
        async with self._reid_merge_lock:
            for cam_id, (frame, timestamp) in frames.items():
                for det in all_detections.get(cam_id, []):
                    try:
                        bbox = det.get("bbox") or []
                        if len(bbox) < 4:
                            continue
                        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                        x, y = max(0, x), max(0, y)
                        crop = frame[y:y+h, x:x+w]
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
                        matches = self.reid.search_gallery(
                            embedding, top_k=1
                        )
                        if matches:
                            global_id = matches[0]["id"]
                            sim = matches[0].get("similarity", 0)
                            # Store a different view (e.g. back/side) only when not already very similar
                            if sim < 0.92:
                                self.reid.add_embedding_to_id(global_id, embedding)
                        elif prev_global and key:
                            # Temporal consistency: same track on same camera keeps same id
                            global_id = prev_global
                        else:
                            global_id = f"PERSON-{self.global_state.total_persons_tracked:05d}"
                            self.reid.add_to_gallery(global_id, embedding)
                            self.global_state.total_persons_tracked += 1
                        if key:
                            self._last_global_by_track[key] = global_id
                        det["global_id"] = global_id
                        results[cam_id].reid_matches.append({
                            "global_id": global_id,
                            "camera_id": cam_id,
                            "track_id": track_id,
                            "bbox": bbox,
                            "timestamp": timestamp,
                        })
                        # Queue for pgvector persistence. Convert to list so we don't
                        # carry numpy refs across the asyncio boundary.
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
            dets = all_detections.get(cam_id, [])
            zone = self._camera_zones.get(cam_id, "default")

            # Crowd density (always on; cheap)
            try:
                crowd_status = self.crowd.update(cam_id, dets)
                results[cam_id].crowd_status = crowd_status
            except Exception as e:
                logger.debug(f"Crowd update error cam {cam_id}: {e}")

            # Fire detection (safety-first — always runs)
            if self._enable_fire and self.fire_detector:
                try:
                    fire_results = await asyncio.to_thread(
                        self.fire_detector.detect, frame, cam_id, zone
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
            total_people = sum(len(d) for d in all_detections.values())
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
                count = result.crowd_status.get("person_count", len(result.detections))
                self.global_state.zone_occupancy[zone] = count

        # Active tracks across all cameras
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
                    rec = self._recorders.get(cam_id)
                    if rec:
                        writer, _ = rec
                        if writer.isOpened():
                            writer.write(vis)
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

            x, y = max(0, x), max(0, y)
            if bw < 1 or bh < 1:
                continue
            bw = min(int(bw), max(1, w - x))
            bh = min(int(bh), max(1, h - y))
            if x >= w or y >= h:
                continue
            color = self._color_for_track(track_id)

            # Bounding box
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)

            # Label: "ClassName #ID  87%"
            tid_str = f"#{track_id}" if track_id is not None else ""
            label = f"{class_name} {tid_str} {conf:.0%}".strip()

            # Filled label background for readability
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.55
            thickness = 1
            (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            label_y = max(th + 6, y)
            label_bg_top = label_y - th - 6
            label_bg_bottom = label_y
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

            # Pose skeleton if available
            if keypoints:
                self._draw_pose_skeleton(frame, keypoints, color)

            # Global ID below bbox (from Re-ID)
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
                "reid": "loaded" if self.reid.model else "mock",
                "reid_gallery_size": len(getattr(self.reid, "_gallery", [])),
                "emotion": "enabled" if self._enable_emotions else "disabled",
                "fire": "enabled" if self._enable_fire else "disabled",
                "shelf": "enabled" if self._enable_shelf else "disabled",
                "checkout": "enabled" if self._enable_checkout else "disabled",
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
            total_detections_snapshot += len(r.detections or [])
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

        # Fire alert history — last 50
        fire_history = (
            self.fire_detector.get_alert_history(limit=50)
            if self.fire_detector else []
        )

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
