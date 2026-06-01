"""
SAM2 on-demand segmenter.

Lazy-loads model only when segmentation endpoint is called.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from loguru import logger


class SAM2Segmenter:
    def __init__(self, weights: str = "sam2_b.pt"):
        self.weights = weights
        self._model = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from ultralytics import SAM
            self._model = SAM(self.weights)
            logger.info(f"SAM2 loaded on demand: {self.weights}")
        except Exception as e:
            logger.warning(f"SAM2 unavailable ({self.weights}): {e}")
            self._model = None

    def segment(
        self,
        frame: np.ndarray,
        bbox: Optional[List[float]] = None,
        points: Optional[List[List[float]]] = None,
    ) -> Dict[str, Any]:
        self._ensure_model()
        if self._model is None:
            return {"ok": False, "reason": "sam2_not_loaded", "masks": []}
        try:
            kwargs: Dict[str, Any] = {"source": frame, "verbose": False}
            if bbox:
                kwargs["bboxes"] = [bbox]
            if points:
                kwargs["points"] = points
            results = self._model(**kwargs)
            masks_out = []
            for r in results:
                m = getattr(r, "masks", None)
                if m is None or getattr(m, "xy", None) is None:
                    continue
                for poly in m.xy:
                    masks_out.append([[float(x), float(y)] for x, y in poly.tolist()])
            return {"ok": True, "masks": masks_out}
        except Exception as e:
            logger.debug(f"SAM2 segment failed: {e}")
            return {"ok": False, "reason": str(e), "masks": []}
