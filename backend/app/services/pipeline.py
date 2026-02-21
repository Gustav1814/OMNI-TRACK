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

WHAT YOU NEED:
  - Camera RTSP URLs (from store IP cameras)
  - OR just use a video file for testing: pipeline.add_camera(1, "test_video.mp4")
  - GPU recommended for real-time (but CPU works at ~5-8 FPS per camera)
"""

import asyncio
import time
import numpy as np
import cv2
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from loguru import logger
import threading
from enum import Enum

# AI Modules
from app.ai.detector import PersonDetector
from app.ai.tracker import MultiObjectTracker
from app.ai.reid import PersonReID
from app.ai.emotion import EmotionRecognizer
from app.ai.fire_detector import FireSmokeDetector
from app.ai.crowd_density import CrowdDensityEstimator
from app.ai.shelf_analytics import ShelfEngagementTracker
from app.ai.checkout_analytics import CheckoutAnalyzer
from app.ai.store_vibe import StoreVibeEngine

# Stream Manager
from app.services.stream_manager import StreamManager, StreamConfig, StreamType
from app.config import settings
from pathlib import Path


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


@dataclass
class GlobalState:
    """
    Shared state across ALL cameras — this is the magic of synchronization.
    
    The Re-ID gallery is GLOBAL: when Cam1 sees a person, their embedding
    goes into the shared gallery. When Cam2 sees someone similar, it finds
    the match. This gives cross-camera tracking.
    """
    total_persons_tracked: int = 0
    active_tracks: Dict[int, Dict[str, Any]] = field(default_factory=dict)  # global_id → info
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

        self.detector = PersonDetector(
            model_path=detector_model,
            confidence=confidence,
            device=device,
        )
        self.tracker = MultiObjectTracker(
            model_path=detector_model,
        )
        self.reid = PersonReID(
            model_name=reid_model,
            device=device if device != "auto" else "cpu",
        )

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

        # --- Processing state ---
        self._processing_task: Optional[asyncio.Task] = None
        self._frame_counts: Dict[int, int] = {}
        self._results_buffer: Dict[int, CameraResult] = {}
        self._callbacks: List[Callable] = []  # Real-time result callbacks
        self._lock = asyncio.Lock()
        # Latest annotated frame (JPEG bytes) per camera for live MJPEG stream
        self._latest_annotated_jpeg: Dict[int, bytes] = {}
        self._jpeg_lock = threading.Lock()
        # Per-camera recording: camera_id -> (VideoWriter, output_path)
        self._recorders: Dict[int, tuple] = {}

        logger.info(f"Pipeline initialized | device={device} | modules: "
                     f"emotion={enable_emotions} fire={enable_fire} "
                     f"shelf={enable_shelf} checkout={enable_checkout}")

    # ─────────────────────────────────────────────────────────────
    # CAMERA MANAGEMENT
    # ─────────────────────────────────────────────────────────────

    def add_camera(
        self,
        camera_id: int,
        source: str,
        stream_type: str = "rtsp",
        zone: str = "default",
        fps: int = 30,
        skip_frames: int = 1,
        roi: Optional[Dict] = None,
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
        config = StreamConfig(
            camera_id=camera_id,
            source=source,
            stream_type=StreamType(stream_type),
            fps_target=fps,
            skip_frames=skip_frames,
            roi=roi,
        )
        self.stream_manager.add_camera(config)
        self._frame_counts[camera_id] = 0

        # Register zone in crowd density
        self.crowd.configure_zone(zone, max_capacity=50)

        logger.info(f"Camera {camera_id} added → zone: {zone}")

    def remove_camera(self, camera_id: int):
        """Remove a camera from processing."""
        self._stop_recording_impl(camera_id)
        self.stream_manager.remove_camera(camera_id)
        self._frame_counts.pop(camera_id, None)
        self._results_buffer.pop(camera_id, None)

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
        return str(path)

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
        logger.info(f"Recording started for camera {camera_id} -> {out_path}")
        return {"recording": True, "path": str(out_path), "camera_id": camera_id}

    def stop_recording(self, camera_id: int) -> Dict[str, Any]:
        """Stop recording and save the clip to footage storage."""
        path = self._stop_recording_impl(camera_id)
        if path:
            logger.info(f"Recording stopped for camera {camera_id} -> {path}")
            return {"recording": False, "saved": path, "camera_id": camera_id}
        return {"recording": False, "message": f"Camera {camera_id} was not recording"}

    def get_recording_status(self) -> Dict[str, Any]:
        """Return which cameras are currently recording."""
        return {
            "recording_cameras": list(self._recorders.keys()),
            "recording": list(self._recorders.keys()),
        }

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

            try:
                # Step 1: Grab frames from ALL cameras simultaneously
                frames = {}
                for cam_id in list(self._frame_counts.keys()):
                    result = await self.stream_manager.get_frame_async(cam_id)
                    if result:
                        frames[cam_id] = result  # (frame, timestamp)

                if not frames:
                    await asyncio.sleep(0.1)
                    continue

                # Step 2-6: Process all frames
                cycle_results = await self._process_synchronized_frames(frames)

                # Step 7: Fire callbacks
                for cb in self._callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(cycle_results, self.global_state)
                        else:
                            cb(cycle_results, self.global_state)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

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
        self, frames: Dict[int, tuple]
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

            # Detect persons
            detections = await asyncio.to_thread(
                self.detector.detect, frame
            )
            all_detections[cam_id] = detections

            # Track persons (maintain IDs across frames for this camera)
            tracks = await asyncio.to_thread(
                self.tracker.update, frame
            )
            all_tracks[cam_id] = tracks

            results[cam_id] = CameraResult(
                camera_id=cam_id,
                timestamp=timestamp,
                frame_number=frame_num,
                detections=detections,
                tracks=tracks,
            )

        # ── PHASE 2: Cross-Camera Re-ID (GLOBAL gallery) ──
        # This is what makes multi-camera tracking work:
        # Extract embeddings from each tracked person and match against
        # the global gallery. If Person A from Cam 1 appears on Cam 2,
        # their embedding will match and they get the same global ID.
        for cam_id, (frame, timestamp) in frames.items():
            for det in all_detections.get(cam_id, []):
                try:
                    bbox = det.get("bbox", det.get("box", []))
                    if len(bbox) >= 4:
                        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                        # Clamp to frame bounds
                        x = max(0, x)
                        y = max(0, y)
                        crop = frame[y:y+h, x:x+w]
                        if crop.size > 0:
                            embedding = await asyncio.to_thread(
                                self.reid.extract_embedding, crop
                            )
                            if embedding is not None:
                                # Search global gallery for match
                                matches = self.reid.search_gallery(
                                    embedding, top_k=1, threshold=0.6
                                )
                                if matches:
                                    global_id = matches[0]["id"]
                                else:
                                    # New person — add to gallery
                                    global_id = f"PERSON-{self.global_state.total_persons_tracked:05d}"
                                    self.reid.add_to_gallery(global_id, embedding)
                                    self.global_state.total_persons_tracked += 1

                                results[cam_id].reid_matches.append({
                                    "global_id": global_id,
                                    "camera_id": cam_id,
                                    "bbox": bbox,
                                    "timestamp": timestamp,
                                })
                except Exception as e:
                    logger.debug(f"Re-ID error cam {cam_id}: {e}")

        # ── PHASE 3: Analytics (parallel across modules) ──
        analytics_tasks = []

        for cam_id, (frame, timestamp) in frames.items():
            dets = all_detections.get(cam_id, [])
            
            # Crowd density
            try:
                crowd_status = self.crowd.update(cam_id, dets)
                results[cam_id].crowd_status = crowd_status
            except Exception:
                pass

            # Fire detection (safety-first — always runs)
            if self._enable_fire and self.fire_detector:
                try:
                    fire_results = await asyncio.to_thread(
                        self.fire_detector.detect, frame
                    )
                    results[cam_id].fire_alerts = fire_results
                    if fire_results:
                        self.global_state.fire_alert_active = True
                        logger.warning(f"🔥 FIRE/SMOKE ALERT on Camera {cam_id}!")
                except Exception:
                    pass

            # Emotion recognition
            if self._enable_emotions and self.emotion:
                try:
                    emotion_data = await asyncio.to_thread(
                        self.emotion.analyze_frame, frame
                    )
                    results[cam_id].emotions = emotion_data
                except Exception:
                    pass

            # Shelf analytics
            if self._enable_shelf and self.shelf_tracker:
                try:
                    shelf_data = self.shelf_tracker.update(cam_id, dets, timestamp)
                    results[cam_id].shelf_data = shelf_data
                except Exception:
                    pass

            # Checkout analytics
            if self._enable_checkout and self.checkout:
                try:
                    checkout_data = self.checkout.update(cam_id, dets, timestamp)
                    results[cam_id].checkout_data = checkout_data
                except Exception:
                    pass

        # ── PHASE 4: Store Vibe Score (aggregate everything) ──
        try:
            # Gather metrics from all cameras
            total_people = sum(len(d) for d in all_detections.values())
            avg_sentiment = 0.0
            sentiment_samples = 0
            total_engagement = 0.0

            for r in results.values():
                if r.emotions and isinstance(r.emotions, dict):
                    sent = r.emotions.get("sentiment_score", 0)
                    if sent:
                        avg_sentiment += sent
                        sentiment_samples += 1
                if r.shelf_data and isinstance(r.shelf_data, dict):
                    total_engagement += r.shelf_data.get("engagement_score", 0)

            if sentiment_samples > 0:
                avg_sentiment /= sentiment_samples

            vibe = self.vibe_engine.calculate(
                sentiment_score=avg_sentiment,
                crowd_count=total_people,
                max_capacity=200,
                engagement_score=total_engagement / max(len(results), 1),
                foot_traffic=total_people,
            )
            self.global_state.vibe_score = vibe.get("overall_score", 0)
        except Exception:
            pass

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
                except Exception as e:
                    logger.debug(f"Annotation draw error cam {cam_id}: {e}")

        # Store in buffer for API access
        self._results_buffer = results

        return results

    def _draw_annotations(self, frame: np.ndarray, result: "CameraResult") -> np.ndarray:
        """Draw detection bboxes, track IDs and labels on frame for live CCTV view."""
        h, w = frame.shape[:2]
        for det in result.detections:
            if hasattr(det, "x"):
                x, y, bw, bh = int(det.x), int(det.y), int(det.w), int(det.h)
                conf = getattr(det, "confidence", 0)
                track_id = getattr(det, "track_id", None)
            else:
                bbox = det.get("bbox", det.get("box", []))
                if len(bbox) < 4:
                    continue
                x, y, bw, bh = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                conf = det.get("confidence", 0)
                track_id = det.get("track_id", "")
            x, y = max(0, x), max(0, y)
            label = f"#{track_id} {conf:.0%}" if track_id else f"{conf:.0%}"
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
            cv2.putText(
                frame, label, (x, max(20, y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA
            )
        for match in result.reid_matches:
            bbox = match.get("bbox", [])
            if len(bbox) < 4:
                continue
            x, y, bw, bh = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            gid = match.get("global_id", "")
            if gid:
                cv2.putText(
                    frame, str(gid), (x, min(h - 5, y + bh + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1, cv2.LINE_AA
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
                "tracker": "loaded" if self.tracker.model else "fallback",
                "reid": "loaded" if self.reid.model else "mock",
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
        }

    def get_latest_results(self, camera_id: Optional[int] = None) -> Any:
        """Get latest processing results."""
        if camera_id:
            return self._results_buffer.get(camera_id)
        return self._results_buffer
