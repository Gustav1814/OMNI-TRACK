"""
OmniTrack AI — Camera Stream Manager
Industry-grade video stream handler supporting:
  - RTSP (IP cameras)
  - HTTP/HTTPS streams
  - Local video files (.mp4, .avi, .mkv)
  - USB Webcams (via device index)

HOW THIS WORKS (for a non-CV person):
  1. IP cameras output a video stream over RTSP (like a live URL)
  2. This manager connects to that URL using OpenCV
  3. It reads frames one-by-one at ~30fps and passes them to AI modules
  4. If a camera disconnects, it auto-reconnects with exponential backoff

WHAT YOU NEED:
  - For testing: just use a .mp4 video file path or webcam (device 0)
  - For production: RTSP URLs from your store's IP cameras
    (e.g. rtsp://admin:password@192.168.1.100:554/stream)
"""

import cv2
import asyncio
import time
import numpy as np
from typing import Optional, Dict, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger
import threading
from queue import Queue, Empty


class StreamType(str, Enum):
    RTSP = "rtsp"
    HTTP = "http"
    FILE = "file"
    WEBCAM = "webcam"


@dataclass
class StreamConfig:
    """Configuration for a single camera stream."""
    camera_id: int
    source: str                    # RTSP URL, file path, or device index as string
    stream_type: StreamType = StreamType.RTSP
    fps_target: int = 30           # Target frames per second
    resolution: tuple = (1920, 1080)
    reconnect_delay: float = 2.0   # Initial reconnect delay (seconds)
    max_reconnect_delay: float = 60.0  # Max delay with exponential backoff
    buffer_size: int = 2           # Frame buffer size (keep low to minimize latency)
    skip_frames: int = 0           # Process every Nth frame (0 = process all)
    roi: Optional[Dict] = None     # Region of interest {"x": 0, "y": 0, "w": 640, "h": 480}


@dataclass
class StreamStats:
    """Real-time statistics for a stream."""
    camera_id: int
    is_connected: bool = False
    fps_actual: float = 0.0
    frames_read: int = 0
    frames_dropped: int = 0
    reconnect_count: int = 0
    last_frame_time: float = 0.0
    resolution: tuple = (0, 0)
    uptime_seconds: float = 0.0
    error_message: Optional[str] = None


class CameraStream:
    """
    Manages a single camera stream with auto-reconnect.
    
    Runs frame capture in a background thread to avoid blocking
    the async event loop. Frames are placed into a bounded queue
    so the AI pipeline always gets the most recent frame.
    """

    def __init__(self, config: StreamConfig):
        self.config = config
        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_queue: Queue = Queue(maxsize=config.buffer_size)
        self._stats = StreamStats(camera_id=config.camera_id)
        self._start_time = 0.0
        self._frame_times: list = []

    @property
    def stats(self) -> StreamStats:
        if self._start_time > 0:
            self._stats.uptime_seconds = time.time() - self._start_time
        return self._stats

    def _build_source(self) -> Any:
        """Convert source string to OpenCV-compatible source."""
        if self.config.stream_type == StreamType.WEBCAM:
            return int(self.config.source)
        return self.config.source

    def _connect(self) -> bool:
        """
        Open the video capture.
        
        For RTSP streams, we set specific OpenCV flags to improve
        reliability and reduce latency.
        """
        try:
            source = self._build_source()
            
            if self.config.stream_type == StreamType.RTSP:
                # Use TCP transport for RTSP (more reliable than UDP)
                self._cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            else:
                self._cap = cv2.VideoCapture(source)

            if not self._cap.isOpened():
                self._stats.error_message = f"Failed to open source: {self.config.source}"
                logger.error(f"[Cam {self.config.camera_id}] {self._stats.error_message}")
                return False

            # Set resolution if supported
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.resolution[0])
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.resolution[1])

            # Read actual resolution
            w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._stats.resolution = (w, h)
            self._stats.is_connected = True
            self._stats.error_message = None

            logger.info(
                f"[Cam {self.config.camera_id}] Connected to {self.config.stream_type.value} "
                f"source — {w}x{h}"
            )
            return True

        except Exception as e:
            self._stats.error_message = str(e)
            logger.error(f"[Cam {self.config.camera_id}] Connection error: {e}")
            return False

    def _capture_loop(self):
        """
        Background thread: continuously reads frames from the camera.
        
        Uses a bounded queue so old frames are dropped if AI processing
        is slower than the camera FPS. This ensures the AI always works
        on the LATEST frame (critical for real-time tracking).
        """
        reconnect_delay = self.config.reconnect_delay
        frame_interval = 1.0 / max(self.config.fps_target, 1)
        skip_counter = 0

        while self._running:
            # Connect/reconnect
            if not self._stats.is_connected:
                if not self._connect():
                    self._stats.reconnect_count += 1
                    logger.warning(
                        f"[Cam {self.config.camera_id}] Reconnecting in {reconnect_delay:.1f}s "
                        f"(attempt #{self._stats.reconnect_count})"
                    )
                    time.sleep(reconnect_delay)
                    # Exponential backoff (2s → 4s → 8s → ... → 60s max)
                    reconnect_delay = min(reconnect_delay * 2, self.config.max_reconnect_delay)
                    continue
                else:
                    reconnect_delay = self.config.reconnect_delay  # Reset on success

            # Read frame
            ret, frame = self._cap.read()
            if not ret:
                # For FILE sources, end-of-stream is normal — loop the video
                # instead of re-opening it forever (which leaks Windows
                # Media Foundation / DirectShow handles after ~2-5 minutes
                # and triggers an OS-level resource error).
                if self.config.stream_type == StreamType.FILE and self._cap:
                    try:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = self._cap.read()
                    except Exception:
                        ret = False
                    if ret:
                        # Successfully looped — fall through to processing.
                        pass
                    else:
                        # File is broken or unreadable; stop instead of
                        # reconnecting forever.
                        self._stats.is_connected = False
                        self._stats.error_message = "End of file reached"
                        logger.info(
                            f"[Cam {self.config.camera_id}] File ended and "
                            "could not be looped — stopping stream."
                        )
                        self._cap.release()
                        self._running = False
                        break
                else:
                    # Live source (RTSP/HTTP/Webcam) — release & reconnect.
                    self._stats.is_connected = False
                    self._stats.error_message = "Frame read failed"
                    if self._cap:
                        self._cap.release()
                        self._cap = None
                    continue

            self._stats.frames_read += 1
            now = time.time()

            # Frame skip (e.g., process every 3rd frame for performance)
            if self.config.skip_frames > 0:
                skip_counter += 1
                if skip_counter % (self.config.skip_frames + 1) != 0:
                    continue

            # Apply ROI crop if configured
            if self.config.roi:
                roi = self.config.roi
                frame = frame[roi["y"]:roi["y"]+roi["h"], roi["x"]:roi["x"]+roi["w"]]

            # Calculate actual FPS
            self._frame_times.append(now)
            # Keep last 30 timestamps for FPS calculation
            self._frame_times = self._frame_times[-30:]
            if len(self._frame_times) > 1:
                elapsed = self._frame_times[-1] - self._frame_times[0]
                self._stats.fps_actual = round(len(self._frame_times) / max(elapsed, 0.001), 1)

            self._stats.last_frame_time = now

            # Put frame in queue (drop oldest if full)
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                    self._stats.frames_dropped += 1
                except Empty:
                    pass
            self._frame_queue.put((frame, now))

            # Frame rate limiting
            time.sleep(max(0, frame_interval - (time.time() - now)))

    def start(self):
        """Start capturing frames in a background thread."""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"[Cam {self.config.camera_id}] Stream started")

    def stop(self):
        """Stop capturing and release resources."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap:
            self._cap.release()
        self._stats.is_connected = False
        logger.info(f"[Cam {self.config.camera_id}] Stream stopped")

    def get_frame(self) -> Optional[tuple]:
        """
        Get the latest frame (non-blocking).
        Returns (frame, timestamp) or None if no frame available.
        """
        try:
            return self._frame_queue.get_nowait()
        except Empty:
            return None

    async def get_frame_async(self, timeout: float = 1.0) -> Optional[tuple]:
        """
        Async wrapper to get a frame without blocking the event loop.
        Uses asyncio.to_thread internally.
        """
        try:
            return await asyncio.to_thread(self._frame_queue.get, timeout=timeout)
        except Empty:
            return None


class StreamManager:
    """
    Manages all camera streams in the system.
    
    Usage:
        manager = StreamManager()
        manager.add_camera(StreamConfig(camera_id=1, source="rtsp://..."))
        manager.start_all()
        
        # In processing loop:
        frame, ts = manager.get_frame(camera_id=1)
    """

    def __init__(self):
        self._streams: Dict[int, CameraStream] = {}
        logger.info("StreamManager initialized")

    def add_camera(self, config: StreamConfig) -> None:
        """Register a camera stream."""
        if config.camera_id in self._streams:
            logger.warning(f"Camera {config.camera_id} already registered, replacing")
            self._streams[config.camera_id].stop()
        self._streams[config.camera_id] = CameraStream(config)
        logger.info(f"Camera {config.camera_id} registered ({config.stream_type.value}: {config.source})")

    def remove_camera(self, camera_id: int) -> None:
        """Unregister and stop a camera stream."""
        if camera_id in self._streams:
            self._streams[camera_id].stop()
            del self._streams[camera_id]

    def start_camera(self, camera_id: int) -> bool:
        """Start a specific camera stream."""
        if camera_id not in self._streams:
            return False
        self._streams[camera_id].start()
        return True

    def stop_camera(self, camera_id: int) -> bool:
        """Stop a specific camera stream."""
        if camera_id not in self._streams:
            return False
        self._streams[camera_id].stop()
        return True

    def start_all(self) -> int:
        """Start all registered camera streams. Returns count started."""
        for stream in self._streams.values():
            stream.start()
        return len(self._streams)

    def stop_all(self) -> None:
        """Stop all camera streams."""
        for stream in self._streams.values():
            stream.stop()

    def get_frame(self, camera_id: int) -> Optional[tuple]:
        """Get the latest frame from a camera."""
        if camera_id not in self._streams:
            return None
        return self._streams[camera_id].get_frame()

    async def get_frame_async(self, camera_id: int) -> Optional[tuple]:
        """Get the latest frame from a camera (async)."""
        if camera_id not in self._streams:
            return None
        return await self._streams[camera_id].get_frame_async()

    def get_all_stats(self) -> Dict[int, StreamStats]:
        """Get stats for all camera streams."""
        return {cid: stream.stats for cid, stream in self._streams.items()}

    def get_stats(self, camera_id: int) -> Optional[StreamStats]:
        """Get stats for a specific camera."""
        if camera_id in self._streams:
            return self._streams[camera_id].stats
        return None

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._streams.values() if s.stats.is_connected)

    @property
    def total_count(self) -> int:
        return len(self._streams)
