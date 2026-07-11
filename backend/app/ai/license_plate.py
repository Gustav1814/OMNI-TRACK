"""
OmniTrack AI — License Plate Recognition

This module integrates the open-source fast-alpr library for
license plate detection + OCR. It provides a thin wrapper around
model selection, inference, and annotation serialization.
"""

import base64
import io
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, get_args

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

from app.config import settings

try:
    from fast_alpr.default_ocr import DefaultOCR, OcrModel
except Exception:
    DefaultOCR = None
    OcrModel = None

DETECTOR_WEIGHT_FILE = "license_plate_detection.pt"
DETECTOR_MODELS: List[str] = [DETECTOR_WEIGHT_FILE]
OCR_MODELS: List[str] = list(get_args(OcrModel)) if OcrModel else [
    "cct-s-v2-global-model",
    "cct-xs-v2-global-model",
    "cct-s-v1-global-model",
    "cct-xs-v1-global-model",
]

if "cct-s-v2-global-model" in OCR_MODELS:
    OCR_MODELS.remove("cct-s-v2-global-model")
    OCR_MODELS.insert(0, "cct-s-v2-global-model")

_DETECTOR_CACHE: Dict[str, YOLO] = {}
_OCR_INSTANCE: Optional[DefaultOCR] = None


def _get_detector(detector_model: str) -> YOLO:
    if detector_model != DETECTOR_WEIGHT_FILE:
        raise ValueError(
            f"Only local detector weight supported: {DETECTOR_WEIGHT_FILE}"
        )

    if detector_model not in _DETECTOR_CACHE:
        model_path = Path(settings.MODEL_WEIGHTS_DIR) / detector_model
        if not model_path.exists():
            raise RuntimeError(
                f"License plate detector weight not found: {model_path}"
            )
        _DETECTOR_CACHE[detector_model] = YOLO(str(model_path))

    return _DETECTOR_CACHE[detector_model]


def _get_ocr(ocr_model: str) -> DefaultOCR:
    global _OCR_INSTANCE
    if DefaultOCR is None:
        raise RuntimeError(
            "fast-alpr is not installed. Install fast-alpr[onnx]>=0.4.0."
        )
    if ocr_model not in OCR_MODELS:
        raise ValueError(f"Unknown OCR model: {ocr_model}")

    if _OCR_INSTANCE is None:
        _OCR_INSTANCE = DefaultOCR(
            hub_ocr_model=ocr_model,
            device="auto",
        )

    return _OCR_INSTANCE


def _normalize_prediction(ocr_result: Any, bbox: List[float], detection_confidence: float) -> Dict[str, Any]:
    text = getattr(ocr_result, "text", None) or ""
    confidence = getattr(ocr_result, "confidence", None) or 0.0
    if isinstance(confidence, list):
        confidence = mean(confidence) if confidence else 0.0

    ocr_confidence = getattr(ocr_result, "region_confidence", None)
    if ocr_confidence is None:
        ocr_confidence = getattr(ocr_result, "confidence", None)
        if isinstance(ocr_confidence, list):
            ocr_confidence = mean(ocr_confidence) if ocr_confidence else 0.0

    text = text.strip() if isinstance(text, str) else str(text)

    return {
        "text": text,
        "confidence": float(detection_confidence),
        "bbox": [float(x) for x in bbox],
        "region": getattr(ocr_result, "region", None),
        "ocr_confidence": float(ocr_confidence) if ocr_confidence is not None else None,
    }


def _draw_annotations(image: np.ndarray, predictions: List[Dict[str, Any]]) -> np.ndarray:
    annotated = image.copy()
    for item in predictions:
        x, y, w, h = [int(round(v)) for v in item["bbox"]]
        label = item["text"] or "Plate"
        score = item["confidence"]
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (14, 159, 255), 2)
        label_text = f"{label} {score:.2f}"
        (text_w, text_h), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(annotated, (x, y - text_h - 6), (x + text_w + 8, y), (14, 159, 255), -1)
        cv2.putText(
            annotated,
            label_text,
            (x + 4, y - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return annotated


def recognize_license_plate_image(file_bytes: bytes, detector_model: str, ocr_model: str) -> Dict[str, Any]:
    image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    rgb_array = np.array(image)
    bgr_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)

    detector = _get_detector(detector_model)
    ocr = _get_ocr(ocr_model)

    results = detector(bgr_array, verbose=False)
    if len(results) == 0:
        raise RuntimeError("License plate inference did not return a result.")

    result = results[0]
    boxes = getattr(result, "boxes", None)
    predictions: List[Dict[str, Any]] = []

    if boxes is not None:
        if hasattr(boxes, "data"):
            detection_array = boxes.data.cpu().numpy() if hasattr(boxes.data, "cpu") else np.array(boxes.data)
        else:
            detection_array = np.array(getattr(boxes, "xyxy", []))

        for row in detection_array:
            if len(row) < 5:
                continue
            x1, y1, x2, y2, object_confidence = [float(v) for v in row[:5]]
            x1, y1, x2, y2 = (
                int(max(round(x1), 0)),
                int(max(round(y1), 0)),
                int(min(round(x2), bgr_array.shape[1] - 1)),
                int(min(round(y2), bgr_array.shape[0] - 1)),
            )
            if x2 <= x1 or y2 <= y1:
                continue

            crop = bgr_array[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            ocr_result = ocr.predict(crop)
            bbox = [x1, y1, x2 - x1, y2 - y1]
            predictions.append(_normalize_prediction(ocr_result, bbox, object_confidence))

    annotated_image = _draw_annotations(bgr_array, predictions)
    ok, encoded = cv2.imencode(".jpg", annotated_image)
    if not ok or encoded is None:
        raise RuntimeError("Failed to encode annotated image.")

    return {
        "detector_model": detector_model,
        "ocr_model": ocr_model,
        "predictions": predictions,
        "annotated_image_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
    }
