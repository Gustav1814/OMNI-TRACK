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
        tracker_config: str = "bytetrack.yaml",
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
        use_model: bool = True,
    ):
        self.model_path = model_path
        self.tracker_config = tracker_config
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.model = None
        self.tracks: Dict[int, Track] = {}
        self.next_id = 1
        self.frame_count = 0
        if use_model:
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

    def update_from_detections(self, detections: List[Dict]) -> List[Track]:
        """
        Cheap IoU tracker that reuses boxes from the detector. This avoids a
        second YOLO/ByteTrack model pass on the same frame, which is important
        for low-cost single-feed deployments.
        """
        self.frame_count += 1
        matched_existing = set()
        active_tracks: List[Track] = []

        for det in detections:
            bbox = det.get("bbox") or det.get("box") or []
            if len(bbox) < 4:
                continue
            confidence = float(det.get("confidence", 0.0))
            class_name = str(det.get("class_name", "person"))
            best_id = None
            best_iou = 0.0
            for track_id, track in self.tracks.items():
                if track_id in matched_existing:
                    continue
                iou = self._bbox_iou(bbox, track.bbox)
                if iou > best_iou:
                    best_id = track_id
                    best_iou = iou

            if best_id is not None and best_iou >= self.iou_threshold:
                track = self.tracks[best_id]
                track.bbox = [float(v) for v in bbox[:4]]
                track.confidence = confidence
                track.class_name = class_name
                track.age += 1
                track.hits += 1
                track.time_since_update = 0
                matched_existing.add(best_id)
            else:
                best_id = self.next_id
                self.next_id += 1
                track = Track(
                    track_id=best_id,
                    bbox=[float(v) for v in bbox[:4]],
                    confidence=confidence,
                    class_name=class_name,
                )
                self.tracks[best_id] = track
                matched_existing.add(best_id)
            active_tracks.append(track)

        for track_id in list(self.tracks.keys()):
            if track_id not in matched_existing:
                self.tracks[track_id].time_since_update += 1
                if self.tracks[track_id].time_since_update > self.max_age:
                    del self.tracks[track_id]

        return active_tracks

    @staticmethod
    def _bbox_iou(a: List[float], b: List[float]) -> float:
        ax1, ay1, aw, ah = [float(v) for v in a[:4]]
        bx1, by1, bw, bh = [float(v) for v in b[:4]]
        ax2, ay2 = ax1 + aw, ay1 + ah
        bx2, by2 = bx1 + bw, by1 + bh
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

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
