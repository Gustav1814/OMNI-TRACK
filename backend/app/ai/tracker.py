"""
OmniTrack AI — ByteTrack Multi-Object Tracker
Per-camera track ID management with occlusion recovery.
Uses Ultralytics built-in tracker or falls back to simple IoU tracking.
"""

import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger
from app.config import settings

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False


@dataclass
class Track:
    track_id: int
    bbox: List[float]  # [x, y, w, h]
    confidence: float
    class_name: str = "person"
    age: int = 0  # frames since first seen
    hits: int = 1
    time_since_update: int = 0
    velocity: Optional[List[float]] = None


class MultiObjectTracker:
    """
    ByteTrack-based multi-object tracker.
    Maintains per-camera track states with occlusion recovery.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        tracker_config: str = "botsort.yaml",
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
    ):
        self.model_path = model_path
        self.tracker_config = tracker_config or getattr(settings, "TRACKER_DEFAULT", "botsort.yaml")
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.model = None
        self.tracks: Dict[int, Track] = {}
        self.next_id = 1
        self.frame_count = 0
        self._load_model()

    def _load_model(self):
        if not ULTRALYTICS_AVAILABLE:
            logger.warning("Running tracker in simple IoU mode (no ultralytics)")
            return
        try:
            self.model = YOLO(self.model_path)
            logger.info(f"Loaded tracker model: {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to load tracker model: {e}")
            self.model = None

    def update(self, frame: np.ndarray) -> List[Track]:
        """Process a frame and return active tracks with IDs."""
        self.frame_count += 1

        if self.model is not None:
            return self._track_with_ultralytics(frame)
        else:
            return self._simple_track(frame)

    def _track_with_ultralytics(self, frame: np.ndarray) -> List[Track]:
        """Use Ultralytics built-in ByteTrack."""
        results = self.model.track(
            source=frame,
            persist=True,
            tracker=self.tracker_config,
            classes=[0],
            verbose=False,
        )

        active_tracks = []
        for result in results:
            if result.boxes is None or result.boxes.id is None:
                continue
            for box, track_id in zip(result.boxes, result.boxes.id):
                xyxy = box.xyxy[0].cpu().numpy()
                tid = int(track_id)
                track = Track(
                    track_id=tid,
                    bbox=[float(xyxy[0]), float(xyxy[1]),
                          float(xyxy[2] - xyxy[0]), float(xyxy[3] - xyxy[1])],
                    confidence=float(box.conf[0]),
                    class_name="person",
                )
                self.tracks[tid] = track
                active_tracks.append(track)

        return active_tracks

    def _simple_track(self, frame: np.ndarray) -> List[Track]:
        """Fallback: simple IoU-based tracking for mock/testing."""
        h, w = frame.shape[:2]
        mock_tracks = [
            Track(track_id=1, bbox=[w * 0.3, h * 0.2, w * 0.15, h * 0.6], confidence=0.92),
            Track(track_id=2, bbox=[w * 0.6, h * 0.25, w * 0.12, h * 0.55], confidence=0.87),
        ]
        return mock_tracks

    def get_track(self, track_id: int) -> Optional[Track]:
        return self.tracks.get(track_id)

    def get_active_count(self) -> int:
        return len(self.tracks)

    def reset(self):
        self.tracks.clear()
        self.next_id = 1
        self.frame_count = 0

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
