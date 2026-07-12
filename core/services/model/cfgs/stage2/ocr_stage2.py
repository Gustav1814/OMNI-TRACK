from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import torch
from numpy import ndarray

from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    FOLLOWED_TO,
    MODEL_ID,
)
from services.model.cfgs.ibase_stage import BaseStage


class OCR_STAGE2(BaseStage):
    """
    Stage 2 - Recognise text inside number plate crops produced by previous stages.

    Parameters
    ----------
    model_path:
        Directory containing EasyOCR weights. If a file path is provided, its parent
        directory is used. Leave empty to rely on EasyOCR's default cache.
    model_id:
        Stage identifier.
    tag:
        Treated as the list of languages to initialise the OCR reader with.
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str],  # noqa: ARG002
        device: str | None = None,
    ):
        super().__init__(model_id)
        self.tag: list[str] = ["en"]
        self.model_path = model_path
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        normalized_device = device.lower()
        self._use_gpu = normalized_device.startswith("cuda") and torch.cuda.is_available()

        try:
            import easyocr  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "EasyOCR is required for the OCR stage. Install it with `pip install easyocr`."
            ) from exc

        storage_dir = self._resolve_storage_dir(model_path)

        # EasyOCR returns results as (bbox, text, confidence)
        self.model = easyocr.Reader(
            self.tag,
            model_storage_directory=str(storage_dir) if storage_dir else None,
            download_enabled=True,
            gpu=self._use_gpu,
        )

    @staticmethod
    def _resolve_storage_dir(model_path: str) -> Path | None:
        if not model_path:
            return None

        resolved = Path(model_path).expanduser().resolve()
        if resolved.is_dir():
            return resolved
        if resolved.exists():
            return resolved.parent
        # Allow missing directories to be created by EasyOCR on demand.
        return resolved if resolved.suffix == "" else resolved.parent

    def forward(
        self,
        image: ndarray,
        prev_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run OCR on the provided image / crop and return detection dictionaries.

        Parameters
        ----------
        image:
            Numpy array (H, W, C) representing the crop to be processed.
        prev_results:
            Outputs from previous pipeline stages. Currently unused but kept for API consistency.
        """

        if not prev_results:
            return []

        for detection in prev_results:
            self._ensure_guid(detection)

            bbox = detection[BBOX]
            x1, y1, x2, y2 = map(int, bbox)
            cropped_imaged = image[y1:y2, x1:x2]
            # TODO crop the image
            if cropped_imaged.size > 0:
                ocr_results = self.model.readtext(cropped_imaged)
                detections: list[dict[str, Any]] = []
                for entry in ocr_results:
                    det = self._convert_detection(entry, x1, y1)
                    self._ensure_guid(det)
                    detections.append(det)
                detection[FOLLOWED_TO] = detections

        return prev_results

    def _convert_detection(self, entry: Any, x_offset: int, y_offset: int) -> dict[str, Any]:
        """
        Convert EasyOCR detection to pipeline format, with coordinate offset applied.
        """
        bbox, text, confidence = entry
        xs = [float(point[0]) + x_offset for point in bbox]
        ys = [float(point[1]) + y_offset for point in bbox]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        text_value = text if text is not None else ""

        return {
            BBOX: list(map(int, [min_x, min_y, max_x, max_y])),
            CONFIDENCE: float(confidence),
            CLASS_ID: 8,
            CLASS_NAME: text_value,
            MODEL_ID: self.model_id,
        }

    @property
    def names(self) -> Sequence[str]:
        return tuple(self.tag)
