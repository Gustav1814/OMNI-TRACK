"""
OmniTrack AI - facial emotion module.

Pipeline per frame: YOLO face detector (face11n) -> largest faces above
EMOTION_MIN_FACE_PX -> HSEmotion EfficientNet-B0 (AffectNet, 8 classes)
on ONNX Runtime, batched on CPU. If either model is missing the module
reports no data (`available` is False). There is no mock mode.

Model choice / optimisation notes (benchmarked on this repo's CAVIAR clips):
  - fp32 ONNX Runtime with full graph optimisation: ~8-12 ms per face on CPU.
  - Static INT8 (QDQ, per-channel, FER2013-calibrated) was rejected: on 1,800
    labelled FER2013 faces every variant was slower than fp32 (b1 11.9-19.3 ms
    vs 8.8 ms) and less accurate (43.7-44.7% vs 46.5%, 79-89% agreement).
  - The face detector dominates cost; its input size adapts to the frame so a
    min-size face stays >=12 px (320 for CCTV-size frames: ~12 ms vs ~29 ms at 640).
  - Faces under ~40 px give near-uniform, "neutral"-biased output, so they
    are skipped instead of being counted.
"""

import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from loguru import logger

from app.config import settings

# HSEmotion enet_b0_8 output order -> dashboard labels
_MODEL_LABELS = ["angry", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_INPUT_SIZE = 224
_FACE_CONF = 0.4
_CROP_MARGIN = 0.15
_FACE_MIN_DET_PX = 12


class EmotionRecognizer:
    """Facial emotion classification for sentiment dashboards."""

    EMOTIONS = list(_MODEL_LABELS)

    def __init__(self, backend: str = "onnx", detector_backend: str = "yolo"):
        self.backend = backend
        self.detector_backend = detector_backend
        self.zone_aggregation: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.min_face_px = max(8, int(settings.EMOTION_MIN_FACE_PX))
        self.max_faces = max(1, int(settings.EMOTION_MAX_FACES_PER_FRAME))
        self._face_model = None
        self._session = None
        self._input_name = "input"
        self._face_half = False
        self._face_lock = threading.Lock()  # ultralytics predict is not thread-safe
        self.available = self._load_models()

    def _load_models(self) -> bool:
        face_path = Path(settings.FACE_MODEL_PATH)
        emo_path = Path(settings.EMOTION_MODEL_PATH)
        missing = [str(p) for p in (face_path, emo_path) if not p.is_file()]
        if missing:
            logger.error(f"EmotionRecognizer: missing weights {missing}; emotion analytics report no data.")
            return False
        try:
            import onnxruntime as ort
            from ultralytics import YOLO

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = max(1, int(settings.EMOTION_THREADS))
            opts.inter_op_num_threads = 1
            providers = ["CPUExecutionProvider"]
            if settings.DEVICE in ("auto", "cuda") and "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")  # needs onnxruntime-gpu
            self._session = ort.InferenceSession(str(emo_path), opts, providers=providers)
            self._input_name = self._session.get_inputs()[0].name
            self._face_model = YOLO(str(face_path), task="detect")
            try:
                import torch
                self._face_half = settings.DEVICE in ("auto", "cuda") and torch.cuda.is_available()
            except ImportError:
                self._face_half = False
        except Exception as e:
            logger.error(f"EmotionRecognizer: failed to load models ({e}); emotion analytics report no data.")
            self._session = self._face_model = None
            return False
        logger.info(
            f"EmotionRecognizer ready: faces={face_path.name} emotion={emo_path.name} "
            f"min_face={self.min_face_px}px max_faces={self.max_faces} threads={settings.EMOTION_THREADS} "
            f"providers={self._session.get_providers()} face_fp16={self._face_half}"
        )
        return True

    def _detect_faces(self, frame: np.ndarray) -> List[List[int]]:
        """Face boxes (x1, y1, x2, y2) large enough to classify, largest first."""
        h, w = frame.shape[:2]
        # Smallest detector input that keeps a min_face_px face at >=12 px (detector misses <~10 px).
        imgsz = int(min(640, max(320, -(-_FACE_MIN_DET_PX * max(h, w) // self.min_face_px // 32) * 32)))
        with self._face_lock:
            res = self._face_model.predict(frame, conf=_FACE_CONF, imgsz=imgsz, half=self._face_half, verbose=False)[0]
        boxes = []
        for x1, y1, x2, y2 in res.boxes.xyxy.cpu().numpy():
            bw, bh = x2 - x1, y2 - y1
            if min(bw, bh) < self.min_face_px:
                continue
            boxes.append([
                int(max(0, x1 - _CROP_MARGIN * bw)), int(max(0, y1 - _CROP_MARGIN * bh)),
                int(min(w, x2 + _CROP_MARGIN * bw)), int(min(h, y2 + _CROP_MARGIN * bh)),
            ])
        boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
        return boxes[: self.max_faces]

    @staticmethod
    def _preprocess(crops: List[np.ndarray]) -> np.ndarray:
        batch = np.empty((len(crops), 3, _INPUT_SIZE, _INPUT_SIZE), dtype=np.float32)
        for i, crop in enumerate(crops):
            rgb = cv2.cvtColor(cv2.resize(crop, (_INPUT_SIZE, _INPUT_SIZE)), cv2.COLOR_BGR2RGB)
            batch[i] = ((rgb.astype(np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)
        return batch

    def classify_faces(self, crops: List[np.ndarray]) -> Optional[np.ndarray]:
        """Softmax probabilities (N x 8) for BGR face crops, one batched run."""
        if not crops or self._session is None:
            return None
        logits = self._session.run(None, {self._input_name: self._preprocess(crops)})[0]
        logits = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        return exp / exp.sum(axis=1, keepdims=True)

    def analyze_frame(
        self, frame: np.ndarray, camera_id: int = 0, zone: str = None
    ) -> List[Dict[str, Any]]:
        """Detect faces and classify their emotion. [] when unavailable or no usable faces."""
        if not self.available or frame is None or frame.ndim != 3:
            return []

        boxes = self._detect_faces(frame)
        crops = [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in boxes]
        probs = self.classify_faces(crops)
        if probs is None:
            return []

        results: List[Dict[str, Any]] = []
        for box, p in zip(boxes, probs):
            k = int(p.argmax())
            result = {
                "dominant_emotion": _MODEL_LABELS[k],
                "confidence": round(float(p[k]), 3),
                "all_emotions": {lbl: round(float(v), 3) for lbl, v in zip(_MODEL_LABELS, p)},
                "bbox": box,
                "camera_id": camera_id,
                "zone": zone,
            }
            results.append(result)
            self._remember_zone_sample(zone, result)
        return results

    def analyze_frame_summary(
        self,
        frame: np.ndarray,
        camera_id: int = 0,
        zone: str = None,
    ) -> Dict[str, Any]:
        """Return a per-frame aggregate summary for pipeline snapshots."""
        faces = self.analyze_frame(frame, camera_id=camera_id, zone=zone)
        if not faces:
            return {
                "camera_id": camera_id,
                "zone": zone,
                "sample_count": 0,
                "dominant_emotion": None,
                "emotion_distribution": {},
                "sentiment_score": 0.0,
            }

        emotion_counts = defaultdict(int)
        for face in faces:
            emotion_counts[face.get("dominant_emotion", "neutral")] += 1
        total = sum(emotion_counts.values())
        dominant = max(emotion_counts, key=emotion_counts.get)
        distribution = {k: round(v / total, 3) for k, v in emotion_counts.items()}
        sentiment = self._sentiment_from_counts(emotion_counts, total)
        return {
            "camera_id": camera_id,
            "zone": zone,
            "sample_count": total,
            "dominant_emotion": dominant,
            "emotion_distribution": distribution,
            "sentiment_score": round(sentiment, 3),
        }

    def get_zone_summary(self, zone: str) -> Dict[str, Any]:
        """Get aggregated emotion summary for a zone."""
        entries = self.zone_aggregation.get(zone, [])
        if not entries:
            return {"zone": zone, "sample_count": 0, "sentiment_score": 0.0}

        emotion_counts = defaultdict(int)
        for entry in entries:
            emotion_counts[entry["dominant_emotion"]] += 1

        total = len(entries)
        distribution = {k: round(v / total, 3) for k, v in emotion_counts.items()}
        dominant = max(emotion_counts, key=emotion_counts.get)
        sentiment = self._sentiment_from_counts(emotion_counts, total)

        return {
            "zone": zone,
            "dominant_emotion": dominant,
            "emotion_distribution": distribution,
            "sample_count": total,
            "sentiment_score": round(sentiment, 3),
        }

    def get_store_sentiment(self) -> Dict[str, Any]:
        """Aggregate sentiment across all zones."""
        all_zones = {}
        total_sentiment = 0.0
        zone_count = 0

        for zone_name in self.zone_aggregation:
            summary = self.get_zone_summary(zone_name)
            all_zones[zone_name] = summary
            total_sentiment += summary["sentiment_score"]
            zone_count += 1

        avg_sentiment = total_sentiment / max(zone_count, 1)
        return {
            "overall_sentiment": round(avg_sentiment, 3),
            "zone_sentiments": all_zones,
            "total_zones": zone_count,
        }

    def _remember_zone_sample(self, zone: str, result: Dict[str, Any]) -> None:
        if not zone:
            return
        self.zone_aggregation[zone].append(result)
        if len(self.zone_aggregation[zone]) > 500:
            self.zone_aggregation[zone] = self.zone_aggregation[zone][-250:]

    @staticmethod
    def _sentiment_from_counts(emotion_counts: Dict[str, int], total: int) -> float:
        positive = emotion_counts.get("happy", 0) + emotion_counts.get("surprise", 0)
        negative = (
            emotion_counts.get("sad", 0)
            + emotion_counts.get("angry", 0)
            + emotion_counts.get("fear", 0)
            + emotion_counts.get("disgust", 0)
            + emotion_counts.get("contempt", 0)
        )
        return (positive - negative) / max(total, 1)

    def reset(self):
        self.zone_aggregation.clear()
