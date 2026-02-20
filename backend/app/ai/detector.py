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
            self.model = YOLO(self.model_path)
            logger.info(f"Loaded YOLO model: {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            self.model = None

    def detect(self, frame: np.ndarray) -> List[BBox]:
        """Run detection on a single frame. Returns list of BBox."""
        if self.model is None:
            return self._mock_detect(frame)

        results = self.model.predict(
            source=frame,
            conf=self.confidence,
            iou=self.nms_threshold,
            classes=self.classes,
            verbose=False,
        )

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                xyxy = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = xyxy
                detections.append(BBox(
                    x=float(x1),
                    y=float(y1),
                    w=float(x2 - x1),
                    h=float(y2 - y1),
                    confidence=float(box.conf[0]),
                    class_id=int(box.cls[0]),
                    class_name="person",
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
