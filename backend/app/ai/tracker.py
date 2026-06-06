"""
OmniTrack AI — ByteTrack Multi-Object Tracker
Per-camera track ID management with occlusion recovery.
Uses Ultralytics built-in tracker or falls back to simple IoU tracking.
"""

import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from loguru import logger

try:
    from ultralytics import YOLO
    from ultralytics.trackers.byte_tracker import BYTETracker
    from ultralytics.utils import IterableSimpleNamespace
    from ultralytics.utils.checks import check_yaml
    import yaml
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    YOLO = None
    BYTETracker = None
    IterableSimpleNamespace = None
    check_yaml = None
    yaml = None
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


class _ByteTrackDetections:
    """Minimal Ultralytics Results-like adapter for BYTETracker.update()."""

    def __init__(self, xywh: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xywh = np.asarray(xywh, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(conf, dtype=np.float32).reshape(-1)
        self.cls = np.asarray(cls, dtype=np.float32).reshape(-1)
        if len(self.xywh):
            half_w = self.xywh[:, 2] / 2.0
            half_h = self.xywh[:, 3] / 2.0
            self.xyxy = np.column_stack((
                self.xywh[:, 0] - half_w,
                self.xywh[:, 1] - half_h,
                self.xywh[:, 0] + half_w,
                self.xywh[:, 1] + half_h,
            )).astype(np.float32)
        else:
            self.xyxy = np.empty((0, 4), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, idx):
        return _ByteTrackDetections(self.xywh[idx], self.conf[idx], self.cls[idx])


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
        classes: Optional[List[int]] = None,
    ):
        self.model_path = model_path
        self.tracker_config = tracker_config
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.classes = classes
        self.model = None
        self.byte_tracker = None
        self.last_backend = "uninitialized"
        self.last_error: Optional[str] = None
        self.tracks: Dict[int, Track] = {}
        self.next_id = 1
        self.frame_count = 0
        if use_model:
            self._load_model()
        self._load_byte_tracker()

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

    def _load_byte_tracker(self):
        """Load the low-level ByteTrack engine for externally supplied detections."""
        if not ULTRALYTICS_AVAILABLE or BYTETracker is None:
            return
        try:
            cfg_path = check_yaml(self.tracker_config)
            with open(cfg_path, "r", encoding="utf-8") as f:
                args = IterableSimpleNamespace(**yaml.safe_load(f))
            self.byte_tracker = BYTETracker(args)
        except Exception as e:
            logger.warning(f"Failed to load detection-driven ByteTrack: {e}")
            self.byte_tracker = None

    def update(self, frame: np.ndarray) -> List[Track]:
        """Process a frame and return active tracks with IDs."""
        self.frame_count += 1

        if self.model is not None:
            return self._track_with_ultralytics(frame)
        else:
            return self._simple_track(frame)

    def update_from_detections(self, detections: List[Dict]) -> List[Track]:
        """
        Track externally supplied detector boxes.
        Uses ByteTrack when available, otherwise falls back to simple IoU tracking.
        """
        if self.byte_tracker is not None:
            tracks = self._bytetrack_from_detections(detections)
            if tracks is not None:
                self.last_backend = "bytetrack"
                self.last_error = None
                return tracks
        self.last_backend = "iou_fallback"
        return self._iou_track_from_detections(detections)

    def _bytetrack_from_detections(self, detections: List[Dict]) -> Optional[List[Track]]:
        self.frame_count += 1
        xywh = []
        conf = []
        cls = []
        class_names: Dict[int, str] = {}
        for det in detections:
            bbox = det.get("bbox") or det.get("box") or []
            if len(bbox) < 4:
                continue
            x, y, w, h = [float(v) for v in bbox[:4]]
            class_id = int(det.get("class_id", 0) or 0)
            xywh.append([x + w / 2.0, y + h / 2.0, w, h])
            conf.append(float(det.get("confidence", 0.0)))
            cls.append(float(class_id))
            class_names[class_id] = str(det.get("class_name", f"class_{class_id}"))

        results = _ByteTrackDetections(
            np.asarray(xywh, dtype=np.float32).reshape(-1, 4),
            np.asarray(conf, dtype=np.float32),
            np.asarray(cls, dtype=np.float32),
        )
        try:
            tracked = self.byte_tracker.update(results)
        except Exception as e:
            logger.warning(f"Detection-driven ByteTrack failed; using IoU tracker: {e}")
            self.last_error = str(e)
            return None

        active_tracks: List[Track] = []
        for row in np.asarray(tracked).reshape(-1, 8):
            x1, y1, x2, y2, track_id, score, class_id, _idx = row.tolist()
            cid = int(class_id)
            tid = int(track_id)
            previous = self.tracks.get(tid)
            track = Track(
                track_id=tid,
                bbox=[float(x1), float(y1), float(x2 - x1), float(y2 - y1)],
                confidence=float(score),
                class_name=class_names.get(cid, f"class_{cid}"),
                age=(previous.age + 1) if previous else 0,
                hits=(previous.hits + 1) if previous else 1,
            )
            self.tracks[track.track_id] = track
            active_tracks.append(track)
        return active_tracks

    def _iou_track_from_detections(self, detections: List[Dict]) -> List[Track]:
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
            conf=0.25,
            verbose=False,
            **({"classes": self.classes} if self.classes is not None else {}),
        )

        active_tracks = []
        for result in results:
            if result.boxes is None or result.boxes.id is None:
                continue
            for box, track_id in zip(result.boxes, result.boxes.id):
                xyxy = box.xyxy[0].cpu().numpy()
                tid = int(track_id)
                class_id = int(box.cls[0]) if getattr(box, "cls", None) is not None else 0
                class_name = self.model.names.get(class_id, f"class_{class_id}") if self.model else "object"
                previous = self.tracks.get(tid)
                track = Track(
                    track_id=tid,
                    bbox=[float(xyxy[0]), float(xyxy[1]),
                          float(xyxy[2] - xyxy[0]), float(xyxy[3] - xyxy[1])],
                    confidence=float(box.conf[0]),
                    class_name=class_name,
                    age=(previous.age + 1) if previous else 0,
                    hits=(previous.hits + 1) if previous else 1,
                )
                self.tracks[tid] = track
                active_tracks.append(track)

        return active_tracks

    def to_detections(self, tracks: List[Track]) -> List[Dict]:
        """Represent current tracks as detection-like dicts for downstream code."""
        return [
            {
                "bbox": [float(v) for v in track.bbox[:4]],
                "confidence": float(track.confidence),
                "class_name": track.class_name,
                "track_id": int(track.track_id),
            }
            for track in tracks
        ]

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
        self.last_backend = "uninitialized"
        self.last_error = None
        self._load_byte_tracker()

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
