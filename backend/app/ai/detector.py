"""
OmniTrack AI — YOLOv8/v11 Person Detector
Real-time person detection using Ultralytics framework.
Configurable model size, confidence/NMS thresholds, batch inference.
"""

import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from loguru import logger

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False
    logger.warning("Ultralytics not installed. Detector will run in mock mode.")

from app.utils.paths import resolve_model_path


@dataclass
class BBox:
    x: float
    y: float
    w: float
    h: float
    confidence: float
    class_id: int = 0
    class_name: str = "person"
    track_id: Optional[int] = None
    # Pose keypoints: list of [x, y, confidence] triplets (COCO-17 for YOLO pose models)
    keypoints: Optional[List[List[float]]] = None


class PersonDetector:
    """
    YOLOv8/v11 person detector.
    Supports configurable model sizes: yolov8n/s/m/l/x or yolo11n/s/m/l/x.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence: float = 0.5,
        nms_threshold: float = 0.45,
        device: str = "auto",
        classes: Optional[List[int]] = None,
    ):
        self.model_path = model_path
        self.confidence = confidence
        self.nms_threshold = nms_threshold
        self.device = device
        self.classes = classes or [0]  # COCO class 0 = person
        self.model = None
        self._load_model()

    def _load_model(self):
        if not ULTRALYTICS_AVAILABLE:
            logger.warning("Running detector in mock mode (no ultralytics)")
            return
        try:
            resolved = resolve_model_path(self.model_path) or self.model_path
            self.model = YOLO(resolved)
            # Detect pose model from filename or task attribute
            self.is_pose_model = "pose" in str(resolved).lower() or \
                getattr(self.model, "task", "") == "pose"
            logger.info(f"Loaded YOLO model: {resolved} (pose={self.is_pose_model})")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            self.model = None
            self.is_pose_model = False

    def get_class_names(self) -> Dict[int, str]:
        """Get all class names that this model can detect."""
        if self.model is None:
            return {0: "person"}  # Default fallback
        return self.model.names

    def detect(self, frame: np.ndarray) -> List[BBox]:
        """Run detection on a single frame. Returns list of BBox."""
        if self.model is None:
            return self._mock_detect(frame)

        # Pose models use all classes (typically just person), detection models filter
        predict_kwargs = dict(
            source=frame,
            conf=self.confidence,
            iou=self.nms_threshold,
            verbose=False,
        )
        if not getattr(self, "is_pose_model", False):
            predict_kwargs["classes"] = self.classes
        results = self.model.predict(**predict_kwargs)

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            # Extract keypoints if pose model — shape: (N, 17, 3) [x, y, conf]
            kpts_data = None
            if getattr(self, "is_pose_model", False) and getattr(result, "keypoints", None) is not None:
                try:
                    kpts_data = result.keypoints.data.cpu().numpy()  # (N, K, 3)
                except Exception:
                    kpts_data = None

            for i, box in enumerate(result.boxes):
                xyxy = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = xyxy
                class_id = int(box.cls[0])
                class_name = self.model.names.get(class_id, f"class_{class_id}") if self.model else "person"
                kpts = None
                if kpts_data is not None and i < len(kpts_data):
                    kpts = [[float(p[0]), float(p[1]), float(p[2])] for p in kpts_data[i]]
                detections.append(BBox(
                    x=float(x1),
                    y=float(y1),
                    w=float(x2 - x1),
                    h=float(y2 - y1),
                    confidence=float(box.conf[0]),
                    class_id=class_id,
                    class_name=class_name,
                    keypoints=kpts,
                ))
        return detections

    def detect_batch(self, frames: List[np.ndarray]) -> List[List[BBox]]:
        """Batch detection on multiple frames."""
        return [self.detect(frame) for frame in frames]

    def _mock_detect(self, frame: np.ndarray) -> List[BBox]:
        """Mock detection for testing without model weights."""
        h, w = frame.shape[:2]
        return [
            BBox(x=w * 0.3, y=h * 0.2, w=w * 0.15, h=h * 0.6, confidence=0.92, class_name="person"),
            BBox(x=w * 0.6, y=h * 0.25, w=w * 0.12, h=h * 0.55, confidence=0.87, class_name="person"),
        ]

    def update_config(self, confidence: float = None, nms_threshold: float = None):
        if confidence is not None:
            self.confidence = confidence
        if nms_threshold is not None:
            self.nms_threshold = nms_threshold

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
