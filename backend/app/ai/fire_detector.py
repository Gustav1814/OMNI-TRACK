"""
OmniTrack AI — Fire & Smoke Detector
Custom YOLO model for real-time fire/smoke detection with instant alerting.
"""

import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
from loguru import logger

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False


@dataclass
class FireAlert:
    alert_type: str  # "fire" or "smoke"
    confidence: float
    bbox: List[float]
    camera_id: int
    timestamp: str
    zone: Optional[str] = None


class FireDetector:
    """
    Fire & Smoke detection using a custom-trained YOLO model.
    Triggers instant alerts on detection.
    """

    FIRE_CLASSES = {0: "fire", 1: "smoke"}

    def __init__(
        self,
        model_path: str = "fire_smoke.pt",
        confidence: float = 0.4,
        device: str = "auto",
    ):
        self.model_path = model_path
        self.confidence = confidence
        self.device = device
        self.model = None
        self.alert_history: List[FireAlert] = []
        self._load_model()

    def _load_model(self):
        if not ULTRALYTICS_AVAILABLE:
            logger.warning("Running fire detector in mock mode")
            return
        try:
            self.model = YOLO(self.model_path)
            logger.info(f"Loaded fire detection model: {self.model_path}")
        except Exception as e:
            logger.warning(f"Fire model not found ({e}). Using mock mode.")
            self.model = None

    def detect(self, frame: np.ndarray, camera_id: int = 0, zone: str = None) -> List[FireAlert]:
        """
        Detect fire/smoke in a frame.
        Returns list of FireAlert objects.
        """
        if self.model is None:
            return []  # No mock fire alerts — safety critical

        results = self.model.predict(
            source=frame,
            conf=self.confidence,
            verbose=False,
        )

        alerts = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                xyxy = box.xyxy[0].cpu().numpy()
                class_id = int(box.cls[0])
                alert = FireAlert(
                    alert_type=self.FIRE_CLASSES.get(class_id, "unknown"),
                    confidence=float(box.conf[0]),
                    bbox=[float(x) for x in xyxy],
                    camera_id=camera_id,
                    zone=zone,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                alerts.append(alert)
                self.alert_history.append(alert)

        return alerts

    def get_alert_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [
            {
                "alert_type": a.alert_type,
                "confidence": a.confidence,
                "bbox": a.bbox,
                "camera_id": a.camera_id,
                "zone": a.zone,
                "timestamp": a.timestamp,
            }
            for a in self.alert_history[-limit:]
        ]

    def clear_history(self):
        self.alert_history.clear()

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
