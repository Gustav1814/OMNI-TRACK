"""
OmniTrack AI — License Plate Recognition

This module integrates the open-source fast-alpr library for
license plate detection + OCR. It provides a thin wrapper around
model selection, inference, and annotation serialization.
"""

import base64
import io
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, get_args

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

from app.ai.tracker import MultiObjectTracker
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


def _normalize_plate_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    normalized = re.sub(r"[^A-Za-z0-9]", "", text).upper().strip()
    return normalized


def _bbox_iou(box_a: List[float], box_b: List[float]) -> float:
    x1, y1, w1, h1 = box_a
    x2, y2, w2, h2 = box_b
    xa1, ya1, xa2, ya2 = x1, y1, x1 + w1, y1 + h1
    xb1, yb1, xb2, yb2 = x2, y2, x2 + w2, y2 + h2

    inter_x1 = max(xa1, xb1)
    inter_y1 = max(ya1, yb1)
    inter_x2 = min(xa2, xb2)
    inter_y2 = min(ya2, yb2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = w1 * h1
    area_b = w2 * h2
    union_area = area_a + area_b - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


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


def _open_video_capture(source: str) -> cv2.VideoCapture:
    if source.lower().startswith("http://") or source.lower().startswith("https://"):
        return cv2.VideoCapture(source)

    if source.lower().startswith("rtsp://"):
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG)

    return cv2.VideoCapture(source)


def _crop_to_base64(image: np.ndarray, bbox: List[float]) -> str:
    x, y, w, h = [int(round(v)) for v in bbox]
    crop = image[y : y + h, x : x + w]
    if crop.size == 0:
        return ""
    ok, encoded = cv2.imencode(".jpg", crop)
    if not ok or encoded is None:
        return ""
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _filter_prediction(prediction: Dict[str, Any], detection_threshold: float, ocr_threshold: float) -> bool:
    if prediction["confidence"] < detection_threshold:
        return False
    if ocr_threshold > 0 and (prediction.get("ocr_confidence") or 0.0) < ocr_threshold:
        return False
    return True


def recognize_license_plate_image(
    file_bytes: bytes,
    detector_model: str,
    ocr_model: str,
    detection_threshold: float = 0.4,
    ocr_threshold: float = 0.0,
) -> Dict[str, Any]:
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
            prediction = _normalize_prediction(ocr_result, bbox, object_confidence)
            if _filter_prediction(prediction, detection_threshold, ocr_threshold):
                predictions.append(prediction)

    annotated_image = _draw_annotations(bgr_array, predictions)
    ok, encoded = cv2.imencode(".jpg", annotated_image)
    if not ok or encoded is None:
        raise RuntimeError("Failed to encode annotated image.")

    return {
        "detector_model": detector_model,
        "ocr_model": ocr_model,
        "source_type": "image",
        "predictions": predictions,
        "snapshots": [
            {
                "text": item["text"],
                "confidence": item["confidence"],
                "ocr_confidence": item.get("ocr_confidence"),
                "bbox": item["bbox"],
                "image_base64": _crop_to_base64(bgr_array, item["bbox"]),
            }
            for item in predictions
        ],
        "frames_processed": 1,
        "plates_detected": len(predictions),
        "annotated_image_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
        "logs": [f"Processed image with {len(predictions)} plate(s) detected."],
    }


VIDEO_MAX_FRAMES = 10000
VIDEO_FRAME_STEP = 1


def recognize_license_plate_video(
    source: str,
    detector_model: str,
    ocr_model: str,
    detection_threshold: float = 0.4,
    ocr_threshold: float = 0.0,
    max_frames: int = VIDEO_MAX_FRAMES,
    frame_step: int = VIDEO_FRAME_STEP,
) -> Dict[str, Any]:
    source_file = source
    capture = _open_video_capture(source)
    if not capture.isOpened():
        if source.lower().startswith("http"):
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
            temp_file.close()
            try:
                urllib.request.urlretrieve(source, temp_file.name)
                capture = _open_video_capture(temp_file.name)
            except Exception as exc:
                raise RuntimeError(f"Unable to open video source: {exc}")
            finally:
                source_file = temp_file.name
        else:
            raise RuntimeError(f"Unable to open video source: {source}")

    detector = _get_detector(detector_model)
    ocr = _get_ocr(ocr_model)
    tracker = MultiObjectTracker(use_model=False)

    track_predictions: Dict[int, Dict[str, Any]] = {}
    track_snapshots: Dict[int, Dict[str, Any]] = {}
    logs: List[str] = []
    frame_count = 0
    processed_frames = 0
    annotated_frame: Optional[np.ndarray] = None

    while processed_frames < max_frames:
        ret, frame = capture.read()
        if not ret:
            break

        frame_count += 1
        if frame_count % frame_step != 0:
            continue

        processed_frames += 1
        logs.append(f"Analyzed frame {processed_frames}...")

        results = detector(frame, verbose=False)
        if len(results) == 0:
            continue

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue

        if hasattr(boxes, "data"):
            detection_array = boxes.data.cpu().numpy() if hasattr(boxes.data, "cpu") else np.array(boxes.data)
        else:
            detection_array = np.array(getattr(boxes, "xyxy", []))

        frame_candidates: List[Dict[str, Any]] = []
        detections_for_tracking: List[Dict[str, Any]] = []
        for row in detection_array:
            if len(row) < 5:
                continue
            x1, y1, x2, y2, object_confidence = [float(v) for v in row[:5]]
            x1, y1, x2, y2 = (
                int(max(round(x1), 0)),
                int(max(round(y1), 0)),
                int(min(round(x2), frame.shape[1] - 1)),
                int(min(round(y2), frame.shape[0] - 1)),
            )
            if x2 <= x1 or y2 <= y1:
                continue

            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            ocr_result = ocr.predict(crop)
            bbox = [x1, y1, x2 - x1, y2 - y1]
            prediction = _normalize_prediction(ocr_result, bbox, object_confidence)
            if not _filter_prediction(prediction, detection_threshold, ocr_threshold):
                continue

            normalized_text = _normalize_plate_text(prediction["text"])
            if not normalized_text:
                continue

            image_base64 = _crop_to_base64(frame, bbox)
            frame_candidates.append({
                "bbox": bbox,
                "confidence": prediction["confidence"],
                "class_id": 0,
                "class_name": "license_plate",
                "prediction": prediction,
                "image_base64": image_base64,
            })
            detections_for_tracking.append({
                "bbox": bbox,
                "confidence": prediction["confidence"],
                "class_id": 0,
                "class_name": "license_plate",
            })

        if detections_for_tracking:
            tracks = tracker.update_from_detections(detections_for_tracking)
            for track in tracks:
                best_match = None
                best_iou = 0.0
                for candidate in frame_candidates:
                    iou = _bbox_iou(track.bbox, candidate["bbox"])
                    if iou > best_iou:
                        best_iou = iou
                        best_match = candidate
                if best_match is None or best_iou < 0.2:
                    continue

                track_id = track.track_id
                current_prediction = best_match["prediction"].copy()
                current_prediction["track_id"] = track_id
                current_snapshot = {
                    "text": current_prediction["text"],
                    "confidence": current_prediction["confidence"],
                    "ocr_confidence": current_prediction.get("ocr_confidence"),
                    "bbox": current_prediction["bbox"],
                    "image_base64": best_match["image_base64"],
                    "track_id": track_id,
                }

                existing_prediction = track_predictions.get(track_id)
                if existing_prediction is None or current_prediction["confidence"] > existing_prediction["confidence"]:
                    track_predictions[track_id] = current_prediction
                    track_snapshots[track_id] = current_snapshot

                frame_candidates = [c for c in frame_candidates if c is not best_match]

        if frame_candidates:
            annotated_frame = _draw_annotations(frame, [c["prediction"] for c in frame_candidates])

    capture.release()
    if source_file != source and os.path.exists(source_file):
        try:
            os.unlink(source_file)
        except Exception:
            pass

    frames_processed = processed_frames
    predictions = list(track_predictions.values())
    snapshots = list(track_snapshots.values())
    plates_detected = len(predictions)
    logs.append(f"Video inference complete: {plates_detected} unique plate(s) found after {frames_processed} frames.")

    annotated_image_base64 = None
    if annotated_frame is not None:
        ok, encoded = cv2.imencode(".jpg", annotated_frame)
        if ok and encoded is not None:
            annotated_image_base64 = base64.b64encode(encoded.tobytes()).decode("ascii")

    return {
        "detector_model": detector_model,
        "ocr_model": ocr_model,
        "source_type": "video",
        "predictions": predictions,
        "snapshots": snapshots,
        "frames_processed": frames_processed,
        "plates_detected": plates_detected,
        "annotated_image_base64": annotated_image_base64,
        "logs": logs,
    }


def stream_license_plate_video(
    source: str,
    detector_model: str,
    ocr_model: str,
    detection_threshold: float = 0.4,
    ocr_threshold: float = 0.0,
    max_frames: int = VIDEO_MAX_FRAMES,
    frame_step: int = VIDEO_FRAME_STEP,
):
    source_file = source
    capture = _open_video_capture(source)
    if not capture.isOpened():
        if source.lower().startswith("http"):
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
            temp_file.close()
            try:
                urllib.request.urlretrieve(source, temp_file.name)
                capture = _open_video_capture(temp_file.name)
            except Exception as exc:
                raise RuntimeError(f"Unable to open video source: {exc}")
            finally:
                source_file = temp_file.name
        else:
            raise RuntimeError(f"Unable to open video source: {source}")

    detector = _get_detector(detector_model)
    ocr = _get_ocr(ocr_model)
    tracker = MultiObjectTracker(use_model=False)

    track_predictions: Dict[int, Dict[str, Any]] = {}
    track_snapshots: Dict[int, Dict[str, Any]] = {}
    frame_count = 0
    processed_frames = 0
    annotated_frame: Optional[np.ndarray] = None

    while processed_frames < max_frames:
        ret, frame = capture.read()
        if not ret:
            break

        frame_count += 1
        if frame_count % frame_step != 0:
            continue

        processed_frames += 1
        logs = [f"Analyzed frame {processed_frames}..."]

        results = detector(frame, verbose=False)
        if len(results) == 0:
            yield {
                "type": "frame",
                "frame": processed_frames,
                "frames_processed": processed_frames,
                "plates_detected": len(track_predictions),
                "predictions": list(track_predictions.values()),
                "snapshots": list(track_snapshots.values()),
                "annotated_image_base64": None,
                "logs": logs,
            }
            continue

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            yield {
                "type": "frame",
                "frame": processed_frames,
                "frames_processed": processed_frames,
                "plates_detected": len(track_predictions),
                "predictions": list(track_predictions.values()),
                "snapshots": list(track_snapshots.values()),
                "annotated_image_base64": None,
                "logs": logs,
            }
            continue

        if hasattr(boxes, "data"):
            detection_array = boxes.data.cpu().numpy() if hasattr(boxes.data, "cpu") else np.array(boxes.data)
        else:
            detection_array = np.array(getattr(boxes, "xyxy", []))

        frame_candidates: List[Dict[str, Any]] = []
        detections_for_tracking: List[Dict[str, Any]] = []
        for row in detection_array:
            if len(row) < 5:
                continue
            x1, y1, x2, y2, object_confidence = [float(v) for v in row[:5]]
            x1, y1, x2, y2 = (
                int(max(round(x1), 0)),
                int(max(round(y1), 0)),
                int(min(round(x2), frame.shape[1] - 1)),
                int(min(round(y2), frame.shape[0] - 1)),
            )
            if x2 <= x1 or y2 <= y1:
                continue

            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            ocr_result = ocr.predict(crop)
            bbox = [x1, y1, x2 - x1, y2 - y1]
            prediction = _normalize_prediction(ocr_result, bbox, object_confidence)
            if not _filter_prediction(prediction, detection_threshold, ocr_threshold):
                continue

            normalized_text = _normalize_plate_text(prediction["text"])
            if not normalized_text:
                continue

            image_base64 = _crop_to_base64(frame, bbox)
            frame_candidates.append({
                "bbox": bbox,
                "confidence": prediction["confidence"],
                "class_id": 0,
                "class_name": "license_plate",
                "prediction": prediction,
                "image_base64": image_base64,
            })
            detections_for_tracking.append({
                "bbox": bbox,
                "confidence": prediction["confidence"],
                "class_id": 0,
                "class_name": "license_plate",
            })

        if detections_for_tracking:
            tracks = tracker.update_from_detections(detections_for_tracking)
            for track in tracks:
                best_match = None
                best_iou = 0.0
                for candidate in frame_candidates:
                    iou = _bbox_iou(track.bbox, candidate["bbox"])
                    if iou > best_iou:
                        best_iou = iou
                        best_match = candidate
                if best_match is None or best_iou < 0.2:
                    continue

                track_id = track.track_id
                current_prediction = best_match["prediction"].copy()
                current_prediction["track_id"] = track_id
                current_snapshot = {
                    "text": current_prediction["text"],
                    "confidence": current_prediction["confidence"],
                    "ocr_confidence": current_prediction.get("ocr_confidence"),
                    "bbox": current_prediction["bbox"],
                    "image_base64": best_match["image_base64"],
                    "track_id": track_id,
                }

                existing_prediction = track_predictions.get(track_id)
                if existing_prediction is None or current_prediction["confidence"] > existing_prediction["confidence"]:
                    track_predictions[track_id] = current_prediction
                    track_snapshots[track_id] = current_snapshot

                frame_candidates = [c for c in frame_candidates if c is not best_match]

        if frame_candidates:
            annotated_frame = _draw_annotations(frame, [c["prediction"] for c in frame_candidates])

        annotated_image_base64 = None
        if annotated_frame is not None:
            ok, encoded = cv2.imencode('.jpg', annotated_frame)
            if ok and encoded is not None:
                annotated_image_base64 = base64.b64encode(encoded.tobytes()).decode('ascii')

        yield {
            "type": "frame",
            "frame": processed_frames,
            "frames_processed": processed_frames,
            "plates_detected": len(track_predictions),
            "predictions": list(track_predictions.values()),
            "snapshots": list(track_snapshots.values()),
            "annotated_image_base64": annotated_image_base64,
            "logs": logs,
        }

    capture.release()
    if source_file != source and os.path.exists(source_file):
        try:
            os.unlink(source_file)
        except Exception:
            pass

    yield {
        "type": "complete",
        "frames_processed": processed_frames,
        "plates_detected": len(track_predictions),
        "predictions": list(track_predictions.values()),
        "snapshots": list(track_snapshots.values()),
        "annotated_image_base64": annotated_image_base64,
        "logs": [f"Video inference complete: {len(track_predictions)} unique plate(s) found after {processed_frames} frames."],
    }
