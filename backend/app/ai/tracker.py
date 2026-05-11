"""
OmniTrack AI — Multi-Object Tracker (ByteTrack / BoT-SORT)

Per-camera track-ID management with occlusion recovery, driven by Ultralytics'
built-in trackers. Works with **any** YOLO `.pt` weight in `model_weight/`:
  - General COCO detectors (yolo11n.pt, yolo26n.pt, yolov8n.pt)  — persons, etc.
  - Domain-specific weights (fire-smoke.pt, face11n.pt, product detection, …)

Two trackers are selectable via `tracker_config`:
  - `bytetrack.yaml`  — fast IoU + two-stage association by score (no Re-ID).
  - `botsort.yaml`    — ByteTrack + camera-motion compensation + (optional) Re-ID.

Class filtering is configurable:
  - `classes=None`  → track every class the loaded model emits (default).
  - `classes=[0]`   → person-only (used by the main pipeline).
  - `classes=[0,1]` → e.g. fire AND smoke for fire-smoke.pt.

If Ultralytics is missing the tracker silently degrades to a simple IoU mock
(testing only).
"""

from pathlib import Path
import numpy as np
from typing import List, Dict, Optional, Iterable
from dataclasses import dataclass
from loguru import logger
from app.config import settings

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

# Ultralytics ships these two configs out of the box. Anything else must be
# a real file path on disk.
_BUILTIN_TRACKERS = {"bytetrack.yaml", "botsort.yaml"}


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


def _resolve_tracker_config(tracker_config: Optional[str]) -> str:
    """
    Normalise a tracker spec into something Ultralytics accepts.

    Accepts:
      - None / empty            → falls back to settings.TRACKER_DEFAULT.
      - 'bytetrack' / 'botsort' → maps to the built-in `*.yaml` name.
      - 'bytetrack.yaml' / 'botsort.yaml' → passed through as-is.
      - Absolute / relative path to a custom yaml → used if the file exists,
        else falls back to default with a warning.
    """
    default = getattr(settings, "TRACKER_DEFAULT", "botsort.yaml") or "botsort.yaml"
    if not tracker_config:
        return default

    spec = str(tracker_config).strip()
    low = spec.lower()

    # Bare short names
    if low in {"bytetrack", "byte-track", "byte_track"}:
        return "bytetrack.yaml"
    if low in {"botsort", "bot-sort", "bot_sort"}:
        return "botsort.yaml"

    # Built-in yaml names
    if low in _BUILTIN_TRACKERS:
        return low

    # Custom yaml file on disk
    p = Path(spec)
    if p.is_file():
        return str(p.resolve())

    logger.warning(
        f"[Tracker] Unknown tracker config '{tracker_config}'. "
        f"Falling back to default '{default}'. "
        f"Valid options: bytetrack.yaml, botsort.yaml, or a path to a custom yaml."
    )
    return default


class MultiObjectTracker:
    """
    Multi-object tracker (ByteTrack / BoT-SORT) wrapping any Ultralytics YOLO
    model. Maintains per-camera track states with occlusion recovery.

    Args:
        model_path: path to any `.pt` weight under `model_weight/` (or absolute).
        tracker_config: 'bytetrack.yaml', 'botsort.yaml', or a custom yaml path.
        classes: iterable of class indices to keep, or None to track every
            class the loaded model emits.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        tracker_config: Optional[str] = None,
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
        classes: Optional[Iterable[int]] = None,
    ):
        self.model_path = model_path
        self.tracker_config = _resolve_tracker_config(tracker_config)
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        # None → all classes the model knows about.
        self.classes: Optional[List[int]] = (
            [int(c) for c in classes] if classes is not None else None
        )
        self.model = None
        self.class_names: Dict[int, str] = {}
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
            try:
                self.class_names = {int(k): str(v) for k, v in (self.model.names or {}).items()}
            except Exception:
                self.class_names = {}
            logger.info(
                f"[Tracker] Loaded '{self.model_path}' — tracker='{self.tracker_config}' "
                f"classes_filter={self.classes if self.classes is not None else 'all'} "
                f"model_classes={list(self.class_names.values())[:6]}"
            )
        except Exception as e:
            logger.error(f"[Tracker] Failed to load model '{self.model_path}': {e}")
            self.model = None

    def update(self, frame: np.ndarray) -> List[Track]:
        """Process a frame and return active tracks with IDs."""
        self.frame_count += 1

        if self.model is not None:
            return self._track_with_ultralytics(frame)
        else:
            return self._simple_track(frame)

    def _track_with_ultralytics(self, frame: np.ndarray) -> List[Track]:
        """Use Ultralytics built-in ByteTrack / BoT-SORT."""
        track_kwargs = dict(
            source=frame,
            persist=True,
            tracker=self.tracker_config,
            verbose=False,
        )
        # Only constrain classes when caller explicitly requested it; otherwise
        # let the model emit all of its classes (so e.g. fire-smoke.pt tracks
        # both fire AND smoke instead of silently dropping smoke).
        if self.classes is not None:
            track_kwargs["classes"] = self.classes

        results = self.model.track(**track_kwargs)

        active_tracks: List[Track] = []
        for result in results:
            if result.boxes is None or result.boxes.id is None:
                continue
            for box, track_id in zip(result.boxes, result.boxes.id):
                xyxy = box.xyxy[0].cpu().numpy()
                tid = int(track_id)
                cls_id = int(box.cls[0]) if box.cls is not None else -1
                cls_name = self.class_names.get(cls_id, str(cls_id))
                track = Track(
                    track_id=tid,
                    bbox=[float(xyxy[0]), float(xyxy[1]),
                          float(xyxy[2] - xyxy[0]), float(xyxy[3] - xyxy[1])],
                    confidence=float(box.conf[0]),
                    class_name=cls_name,
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
