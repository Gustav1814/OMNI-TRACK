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
  3. It keeps only the newest frame, decoded at the pipeline's target FPS
  4. If a camera disconnects, it auto-reconnects with exponential backoff

WHAT YOU NEED:
  - For testing: just use a .mp4 video file path or webcam (device 0)
  - For production: RTSP URLs from your store's IP cameras
    (e.g. rtsp://admin:password@192.168.1.100:554/stream)
"""

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from app.security.input_safety import redact_source, validate_camera_source


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
    fps_target: float = 30         # Frames per second delivered to the pipeline
    resolution: tuple = (1920, 1080)
    reconnect_delay: float = 2.0   # Initial reconnect delay (seconds)
    max_reconnect_delay: float = 60.0  # Max delay with exponential backoff
    buffer_size: int = 2           # Unused (only the newest frame is kept); kept for API compatibility
    skip_frames: int = 0           # Legacy decimation on top of fps_target; the pipeline passes 0
    roi: Optional[Dict] = None     # Region of interest {"x": 0, "y": 0, "w": 640, "h": 480}
    loop: bool = False             # Files only: replay from frame 0 at EOF (new epoch). Off: stop at EOF.


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
    finished: bool = False         # a finite source reached its end (loop off); the stream has stopped
    finished_at: Optional[float] = None
    loops_completed: int = 0       # files with loop on: completed passes


@dataclass
class FramePacket:
    """One decoded frame. ``seq`` increases per stream; ``epoch`` changes on a
    timeline break (reconnect, or a video file looping) so trackers can reset."""
    frame: np.ndarray
    ts: float
    seq: int
    epoch: int


class CameraStream:
    """
    One camera, captured on its own thread.

    Only the newest decoded frame is kept (no queue), so the pipeline always
    works on the present and never on a backlog.

    * Live sources (RTSP/HTTP/webcam): ``grab()`` runs continuously so the
      OpenCV/FFmpeg buffer never lags behind the camera; ``retrieve()`` (the
      colour conversion + copy) only runs when the next frame is due at
      ``fps_target``.
    * Files are replayed on a wall clock at their native FPS so they behave
      like a live camera. At the end of the file the stream either stops and
      reports ``finished`` (default), or, with ``loop=True``, starts again from
      frame 0 under a new ``epoch`` so nothing is merged across passes.
    """

    def __init__(self, config: StreamConfig):
        self.config = config
        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stats = StreamStats(camera_id=config.camera_id)
        self._start_time = 0.0
        self._frame_times: deque = deque(maxlen=30)
        self._grab_times: deque = deque(maxlen=60)
        self._lock = threading.Lock()
        self._latest: Optional[FramePacket] = None
        self._seq = 0
        self._epoch = 0
        self._read_seq = 0  # last seq handed out by the consuming get_frame()
        self._native_fps = 25.0
        self._play_start = 0.0
        self._play_index = 0
        self._next_due = 0.0
        self._skip_counter = 0

    @property
    def stats(self) -> StreamStats:
        if self._start_time > 0:
            self._stats.uptime_seconds = time.time() - self._start_time
        return self._stats

    @property
    def is_file(self) -> bool:
        return self.config.stream_type == StreamType.FILE

    @property
    def source_fps(self) -> float:
        """Native FPS of a file, or the measured grab rate of a live source."""
        if self.is_file:
            return float(self._native_fps)
        g = self._grab_times
        if len(g) > 1 and g[-1] > g[0]:
            return round((len(g) - 1) / (g[-1] - g[0]), 1)
        return 0.0

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def finished(self) -> bool:
        """True once a finite source has ended and the capture thread has stopped."""
        return self._stats.finished

    def set_fps_target(self, fps: float) -> None:
        self.config.fps_target = max(0.2, float(fps))

    def _build_source(self) -> Any:
        if self.config.stream_type == StreamType.WEBCAM:
            return int(self.config.source)
        return self.config.source

    def _connect(self) -> bool:
        try:
            source = self._build_source()
            if self.config.stream_type == StreamType.RTSP:
                self._cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            else:
                self._cap = cv2.VideoCapture(source)
            if not self._cap.isOpened():
                self._stats.error_message = f"Failed to open source: {redact_source(self.config.source)}"
                logger.error(f"[Cam {self.config.camera_id}] {self._stats.error_message}")
                return False
            if self.config.stream_type == StreamType.WEBCAM:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.resolution[0])
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.resolution[1])
            w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._stats.resolution = (w, h)
            if self.is_file:
                native = self._cap.get(cv2.CAP_PROP_FPS) or 0.0
                self._native_fps = native if 1.0 <= native <= 120.0 else 25.0
                self._play_start = time.time()
                self._play_index = 0
            self._next_due = 0.0
            self._stats.is_connected = True
            self._stats.error_message = None
            logger.info(
                f"[Cam {self.config.camera_id}] Connected to {self.config.stream_type.value} source - {w}x{h}"
            )
            return True
        except Exception as e:
            self._stats.error_message = str(e)
            logger.error(f"[Cam {self.config.camera_id}] Connection error: {e}")
            return False

    def _publish(self, frame: np.ndarray, now: float) -> None:
        if self.config.skip_frames > 0:  # legacy option; the pipeline uses fps_target only
            self._skip_counter += 1
            if self._skip_counter % (self.config.skip_frames + 1) != 0:
                return
        if self.config.roi:
            roi = self.config.roi
            frame = frame[roi["y"]:roi["y"] + roi["h"], roi["x"]:roi["x"] + roi["w"]]
        with self._lock:
            if self._latest is not None and self._latest.seq > self._read_seq:
                self._stats.frames_dropped += 1  # previous frame was never consumed
            self._seq += 1
            self._latest = FramePacket(frame, now, self._seq, self._epoch)
        self._frame_times.append(now)
        ft = self._frame_times
        if len(ft) > 1:
            self._stats.fps_actual = round((len(ft) - 1) / max(ft[-1] - ft[0], 1e-3), 1)
        self._stats.last_frame_time = now

    def _due(self, now: float) -> bool:
        if now < self._next_due:
            return False
        interval = 1.0 / max(self.config.fps_target, 0.2)
        # stay on the grid but never accumulate debt after a stall
        self._next_due = max(self._next_due + interval, now + 0.5 * interval)
        return True

    def _disconnect(self, message: str) -> None:
        self._stats.is_connected = False
        self._stats.error_message = message
        self._epoch += 1
        if self._cap:
            self._cap.release()

    def _step_live(self) -> None:
        if not self._cap.grab():
            self._disconnect("Frame grab failed")
            return
        now = time.time()
        self._stats.frames_read += 1
        self._grab_times.append(now)
        if self._due(now):
            ok, frame = self._cap.retrieve()
            if ok and frame is not None:
                self._publish(frame, now)

    def _step_file(self) -> None:
        now = time.time()
        if self._next_due > now:
            time.sleep(min(self._next_due - now, 0.25))
            return
        due_idx = int((now - self._play_start) * self._native_fps)
        if self._play_index > due_idx:  # ahead of the wall clock
            time.sleep(min((self._play_index - due_idx) / self._native_fps, 0.25))
            return
        ok = True
        while ok and self._play_index <= due_idx:  # decode up to "now" without converting
            ok = self._cap.grab()
            if ok:
                self._play_index += 1
                self._stats.frames_read += 1
        if not ok:
            self._end_of_file()
            return
        if self._due(now):
            ok, frame = self._cap.retrieve()
            if ok and frame is not None:
                self._publish(frame, now)

    def _end_of_file(self) -> None:
        """EOF of a file: replay under a new epoch (loop on) or stop and report finished."""
        if self.config.loop:
            # Reopen rather than seek: seeking to 0 is unreliable for MPEG-PS (.mpg) and some AVI files.
            self._cap.release()
            if not self._connect():  # resets play clock/index; capture loop retries if this failed
                self._stats.is_connected = False
            self._epoch += 1  # timeline break: trackers/analytics must not merge passes
            self._stats.loops_completed += 1
            logger.info(f"[Cam {self.config.camera_id}] end of file, looping (pass {self._stats.loops_completed + 1})")
            return
        self._running = False
        self._epoch += 1  # a later restart is a new timeline
        self._stats.finished = True
        self._stats.finished_at = time.time()
        self._stats.is_connected = False
        self._stats.error_message = None
        if self._cap:
            self._cap.release()
        logger.info(
            f"[Cam {self.config.camera_id}] end of file after {self._play_index} frames; stream finished (loop off)"
        )

    def _capture_loop(self):
        reconnect_delay = self.config.reconnect_delay
        while self._running:
            if not self._stats.is_connected:
                if not self._connect():
                    self._stats.reconnect_count += 1
                    logger.warning(
                        f"[Cam {self.config.camera_id}] Reconnecting in {reconnect_delay:.1f}s "
                        f"(attempt #{self._stats.reconnect_count})"
                    )
                    end = time.time() + reconnect_delay
                    while self._running and time.time() < end:
                        time.sleep(0.2)
                    reconnect_delay = min(reconnect_delay * 2, self.config.max_reconnect_delay)
                    continue
                reconnect_delay = self.config.reconnect_delay
            try:
                if self.is_file:
                    self._step_file()
                else:
                    self._step_live()
            except Exception as e:  # pragma: no cover - driver specific
                logger.error(f"[Cam {self.config.camera_id}] capture error: {e}")
                self._disconnect(str(e))

    def start(self):
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._stats.finished = False
        self._stats.finished_at = None
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name=f"cam-{self.config.camera_id}")
        self._thread.start()
        logger.info(f"[Cam {self.config.camera_id}] Stream started")

    def stop(self):
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        if self._cap:
            self._cap.release()
        self._stats.is_connected = False
        logger.info(f"[Cam {self.config.camera_id}] Stream stopped")

    def get_latest(self, after_seq: int = 0) -> Optional[FramePacket]:
        """Newest frame if it is newer than ``after_seq`` (non-consuming, non-blocking)."""
        with self._lock:
            pkt = self._latest
            if pkt is None or pkt.seq <= after_seq:
                return None
            self._read_seq = max(self._read_seq, pkt.seq)
            return pkt

    def get_frame(self) -> Optional[tuple]:
        """Newest unread frame as ``(frame, ts)`` or None (non-blocking)."""
        pkt = self.get_latest(self._read_seq)
        return (pkt.frame, pkt.ts) if pkt else None

    async def get_frame_async(self, timeout: float = 1.0) -> Optional[tuple]:
        end = time.time() + timeout
        while True:
            got = self.get_frame()
            if got is not None or time.time() >= end:
                return got
            await asyncio.sleep(0.01)


class FrameRing:
    """Recent frames of one camera for evidence clips: JPEG-encoded, downscaled to <= max_width,
    bounded by both seconds and bytes. push() runs off the frame loop (ring executor)."""

    def __init__(self, seconds: float, max_bytes: int, fps: float, max_width: int, quality: int = 80):
        self.seconds = float(seconds)
        self.max_bytes = int(max_bytes)
        self.interval = 1.0 / max(0.1, float(fps))
        self.max_width = int(max_width)
        self.quality = int(quality)
        self._items: Deque[Tuple[float, bytes]] = deque()
        self._bytes = 0
        self._lock = threading.Lock()
        self._last_ts = float("-inf")
        self.busy = False  # an encode for this camera is queued / running

    def due(self, ts: float) -> bool:
        return not self.busy and ts - self._last_ts >= self.interval

    def push(self, frame: np.ndarray, ts: float) -> None:
        try:
            h, w = frame.shape[:2]
            if w > self.max_width:
                frame = cv2.resize(frame, (self.max_width, max(1, int(round(h * self.max_width / w)))),
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
            if not ok:
                return
            data = buf.tobytes()
            with self._lock:
                if ts < self._last_ts:  # timeline restarted (file loop): old frames are another epoch
                    self._items.clear()
                    self._bytes = 0
                self._items.append((float(ts), data))
                self._bytes += len(data)
                self._last_ts = float(ts)
                cut = ts - self.seconds
                while self._items and (self._items[0][0] < cut or self._bytes > self.max_bytes):
                    self._bytes -= len(self._items.popleft()[1])
        finally:
            self.busy = False

    def frames(self, t_from: float, t_to: float) -> List[Tuple[float, np.ndarray]]:
        """frame_provider for app.services.evidence: decoded BGR frames with t_from <= ts <= t_to."""
        with self._lock:
            sel = [(t, b) for t, b in self._items if t_from <= t <= t_to]
        out = []
        for t, b in sel:
            img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out.append((t, img))
        return out

    def set_max_bytes(self, max_bytes: int) -> None:
        with self._lock:
            self.max_bytes = int(max_bytes)
            while self._items and self._bytes > self.max_bytes:
                self._bytes -= len(self._items.popleft()[1])

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            span = self._items[-1][0] - self._items[0][0] if len(self._items) > 1 else 0.0
            return {"frames": len(self._items), "bytes": self._bytes, "seconds": round(span, 1),
                    "max_bytes": self.max_bytes}


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
        """Register a camera stream. The source is re-validated here (SSRF / path traversal)
        so no caller can open an arbitrary file or internal URL; raises UnsafeInputError."""
        config.source = validate_camera_source(str(config.source))
        if config.camera_id in self._streams:
            logger.warning(f"Camera {config.camera_id} already registered, replacing")
            self._streams[config.camera_id].stop()
        self._streams[config.camera_id] = CameraStream(config)
        logger.info(f"Camera {config.camera_id} registered ({config.stream_type.value}: {redact_source(config.source)})")

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

    def has_camera(self, camera_id: int) -> bool:
        """Return whether a camera is currently registered."""
        return camera_id in self._streams

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._streams.values() if s.stats.is_connected)

    @property
    def total_count(self) -> int:
        return len(self._streams)
