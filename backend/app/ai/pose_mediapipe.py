"""
Optional MediaPipe pose extractor (free, CPU-friendly).
Runs on-demand to avoid constant per-frame overhead.
"""

from __future__ import annotations

from typing import Any, Dict, List

import cv2
import numpy as np
from loguru import logger


class MediaPipePose:
    def __init__(self):
        self._pose = None
        self._mp = None
        try:
            import mediapipe as mp  # type: ignore

            self._mp = mp
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                enable_segmentation=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        except Exception as e:
            logger.warning(f"MediaPipe unavailable: {e}")

    @property
    def available(self) -> bool:
        return self._pose is not None

    def run(self, frame_bgr: np.ndarray) -> Dict[str, Any]:
        if self._pose is None:
            return {"ok": False, "landmarks": [], "reason": "mediapipe_not_available"}
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._pose.process(rgb)
        if not result.pose_landmarks:
            return {"ok": True, "landmarks": []}
        h, w = frame_bgr.shape[:2]
        landmarks: List[List[float]] = []
        for lm in result.pose_landmarks.landmark:
            landmarks.append([float(lm.x * w), float(lm.y * h), float(lm.visibility)])
        return {"ok": True, "landmarks": landmarks}
