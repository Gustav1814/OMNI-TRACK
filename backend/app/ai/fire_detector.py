"""
OmniTrack AI — Fire & Smoke Detector

Production YOLO-based fire/smoke detector. Safety-first: never emits mock
alerts — if a fire-specific model is not loaded, `detect` returns an empty list
instead of faking an alert. The operator sees this through the status payload
(`is_fire_specific=False`) and must deploy a real model for production.

Training a custom model:
  1. Collect labelled fire/smoke imagery (PyroNear, FIRE-SMOKE-DATASET, …)
  2. `yolo detect train model=yolov8n.pt data=fire.yaml epochs=100 imgsz=640`
  3. Copy the resulting best.pt to `backend/fire_smoke.pt`
  4. Set FIRE_MODEL_PATH=fire_smoke.pt in `.env`

The detector auto-inspects the loaded model's class names and flips into
"fire-specific" mode only when fire/smoke classes are present; otherwise it
refuses to produce alerts (prevents false positives from a generic COCO YOLO).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set

import numpy as np
from dataclasses import dataclass
from loguru import logger

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except Exception:  # pragma: no cover
    ULTRALYTICS_AVAILABLE = False
    YOLO = None  # type: ignore


# Canonical fire/smoke class names we accept from any fire-trained model.
# Different community datasets use slightly different names.
_FIRE_KEYWORDS: Set[str] = {"fire", "flame", "flames"}
_SMOKE_KEYWORDS: Set[str] = {"smoke", "smog", "fog-smoke"}

# Standard COCO / generic Ultralytics checkpoints — never load these for fire alerts
# (even if misconfigured in .env); use a dedicated fire/smoke-trained .pt only.
_FORBIDDEN_FIRE_WEIGHT_BASENAMES: frozenset[str] = frozenset(
    {
        "yolo11n.pt",
        "yolo11s.pt",
        "yolo11m.pt",
        "yolo11l.pt",
        "yolo11x.pt",
        "yolov8n.pt",
        "yolov8s.pt",
        "yolov8m.pt",
        "yolov8l.pt",
        "yolov8x.pt",
        "yolov9t.pt",
        "yolov9s.pt",
        "yolov9m.pt",
        "yolov9c.pt",
        "yolov9e.pt",
        "yolov10n.pt",
        "yolov10s.pt",
        "yolov10m.pt",
        "yolov10b.pt",
        "yolov10l.pt",
        "yolov10x.pt",
        "yolo26n.pt",
        "yolo26s.pt",
        "yolo26m.pt",
        "yolo26l.pt",
        "yolo26x.pt",
        "yolov5n.pt",
        "yolov5s.pt",
        "yolov5m.pt",
        "yolov5l.pt",
        "yolov5x.pt",
        "yolov5nu.pt",
        "yolov5su.pt",
        "yolov5mu.pt",
        "yolov5lu.pt",
        "yolov5xu.pt",
        "yolo12n.pt",
        "yolo12s.pt",
        "yolo12m.pt",
        "yolo12l.pt",
        "yolo12x.pt",
    }
)


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
    Triggers instant alerts on detection and keeps a bounded history.
    """

    # Default numeric mapping used when a model exposes fire/smoke as ids 0/1.
    DEFAULT_NUMERIC_MAP = {0: "fire", 1: "smoke"}

    def __init__(
        self,
        model_path: str = "fire-smoke.pt",
        confidence: float = 0.58,
        device: str = "auto",
        history_limit: int = 500,
    ):
        self.model_path = model_path
        self.confidence = confidence
        self.device = device
        self.model = None
        self.model_class_map: Dict[int, str] = {}
        self.is_fire_specific: bool = False  # Flipped to True once model classes verified
        self.alert_history: List[FireAlert] = []
        self._history_limit = max(100, history_limit)
        self._load_model()

    # ─────────────────────────────────────────────────────────────
    # Model loading + class inspection
    # ─────────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        if not ULTRALYTICS_AVAILABLE:
            logger.warning("[FireDetector] Ultralytics unavailable — detector disabled.")
            return

        path = self.model_path
        base = os.path.basename(path).lower()
        if base in _FORBIDDEN_FIRE_WEIGHT_BASENAMES:
            logger.warning(
                f"[FireDetector] Refusing to load generic YOLO weights as fire model ({base!r}). "
                "Set FIRE_MODEL_PATH to a dedicated fire/smoke-trained checkpoint."
            )
            self.model = None
            return

        if not os.path.isabs(path):
            # Allow looking inside CWD first, then `backend/` for dev convenience
            if not os.path.isfile(path) and os.path.isfile(os.path.join(os.getcwd(), path)):
                path = os.path.join(os.getcwd(), path)
        try:
            self.model = YOLO(path)
        except Exception as e:
            logger.warning(
                f"[FireDetector] Failed to load model '{self.model_path}' ({e}). "
                "Detector will report no alerts until a fire-specific model is deployed."
            )
            self.model = None
            return

        # Inspect class names
        names = {}
        try:
            names = dict(getattr(self.model, "names", {}) or {})
        except Exception:
            names = {}
        self.model_class_map = {int(k): str(v).lower() for k, v in names.items()}
        lower_names = set(self.model_class_map.values())
        has_fire = any(n in _FIRE_KEYWORDS for n in lower_names)
        has_smoke = any(n in _SMOKE_KEYWORDS for n in lower_names)
        self.is_fire_specific = bool(has_fire or has_smoke)

        if self.is_fire_specific:
            logger.info(
                f"[FireDetector] Loaded fire-specific model '{self.model_path}' "
                f"(classes={self.model_class_map})"
            )
        else:
            logger.warning(
                f"[FireDetector] Loaded model '{self.model_path}' but it is NOT fire-specific "
                f"(classes={list(lower_names)[:5]}...). "
                "Detector will NOT raise alerts — deploy a custom YOLO trained on fire/smoke data "
                "and set FIRE_MODEL_PATH in .env."
            )

    # ─────────────────────────────────────────────────────────────
    # Detection
    # ─────────────────────────────────────────────────────────────

    def detect(
        self,
        frame: np.ndarray,
        camera_id: int = 0,
        zone: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Detect fire/smoke in a frame. Returns JSON-serialisable dicts suitable
        for attaching directly to `CameraResult.fire_alerts`.
        Safety: never produces alerts when the model is generic (not fire-trained).
        """
        if self.model is None or not self.is_fire_specific:
            return []

        try:
            results = self.model.predict(
                source=frame,
                conf=self.confidence,
                verbose=False,
            )
        except Exception as e:
            logger.debug(f"[FireDetector] predict failed: {e}")
            return []

        alerts: List[Dict[str, Any]] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                try:
                    class_id = int(box.cls[0])
                    class_name = self.model_class_map.get(
                        class_id, self.DEFAULT_NUMERIC_MAP.get(class_id, "unknown")
                    )
                    if class_name not in _FIRE_KEYWORDS and class_name not in _SMOKE_KEYWORDS:
                        continue
                    alert_type = "fire" if class_name in _FIRE_KEYWORDS else "smoke"
                    xyxy = box.xyxy[0].cpu().numpy()
                    alert = FireAlert(
                        alert_type=alert_type,
                        confidence=float(box.conf[0]),
                        bbox=[float(x) for x in xyxy],
                        camera_id=camera_id,
                        zone=zone,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    self._record_alert(alert)
                    alerts.append({
                        "alert_type": alert.alert_type,
                        "confidence": alert.confidence,
                        "bbox": alert.bbox,
                        "camera_id": alert.camera_id,
                        "zone": alert.zone,
                        "timestamp": alert.timestamp,
                    })
                except Exception as e:
                    logger.debug(f"[FireDetector] box parse failed: {e}")
                    continue
        return alerts

    def _record_alert(self, alert: FireAlert) -> None:
        self.alert_history.append(alert)
        if len(self.alert_history) > self._history_limit:
            self.alert_history = self.alert_history[-self._history_limit // 2:]

    # ─────────────────────────────────────────────────────────────
    # Status / history
    # ─────────────────────────────────────────────────────────────

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

    def clear_history(self) -> None:
        self.alert_history.clear()

    def get_status(self) -> Dict[str, Any]:
        return {
            "model_path": self.model_path,
            "is_loaded": self.model is not None,
            "is_fire_specific": self.is_fire_specific,
            "classes": self.model_class_map,
            "confidence_threshold": self.confidence,
            "history_size": len(self.alert_history),
        }

    @property
    def is_loaded(self) -> bool:
        return self.model is not None


# Back-compat alias — the pipeline imports `FireSmokeDetector`.
FireSmokeDetector = FireDetector
