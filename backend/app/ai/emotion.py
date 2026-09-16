"""
OmniTrack AI — Emotion Recognition Module
DeepFace/FER-based facial emotion classification.
7-class: happy, sad, angry, surprise, neutral, fear, disgust.
"""

import sys

import numpy as np
from typing import List, Dict, Any, Optional
from collections import defaultdict
from loguru import logger

from app.config import settings

# DeepFace's logger prints an emoji (U+26A0) the moment it is imported. Windows
# defaults stdout to cp1252, which cannot encode it, so the import dies with
# UnicodeEncodeError and the module silently falls back to mock mode — with a
# message that makes it look like the package is missing. Force UTF-8 first.
for _stream in (sys.stdout, sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except Exception as _deepface_err:
    # Catches ImportError AND ValueError raised by retinaface when tf-keras is missing
    # (TF 2.16+ split keras; retinaface crashes hard at import time otherwise).
    DEEPFACE_AVAILABLE = False
    DeepFace = None  # type: ignore
    logger.warning(
        f"DeepFace not available ({type(_deepface_err).__name__}). "
        "Emotion module will run in mock mode."
    )


class EmotionRecognizer:
    """
    Emotion recognition using DeepFace/FER.
    Detects faces, classifies emotions, aggregates per zone.
    """

    EMOTIONS = ["happy", "sad", "angry", "surprise", "neutral", "fear", "disgust"]

    def __init__(self, backend: str = "opencv", detector_backend: Optional[str] = None):
        # Default comes from config so the backend can be tuned without a code change.
        self.backend = backend
        self.detector_backend = detector_backend or getattr(
            settings, "EMOTION_DETECTOR_BACKEND", "ssd"
        )
        self.zone_aggregation: Dict[str, List[Dict]] = defaultdict(list)

    def analyze_frame(
        self, frame: np.ndarray, camera_id: int = 0, zone: str = None
    ) -> List[Dict[str, Any]]:
        """
        Analyze emotions in a frame.
        Returns list of emotion results per detected face.
        """
        if not DEEPFACE_AVAILABLE:
            return self._mock_analyze(camera_id, zone)

        try:
            results = DeepFace.analyze(
                img_path=frame,
                actions=["emotion"],
                enforce_detection=False,
                detector_backend=self.detector_backend,
                silent=True,
            )

            if not isinstance(results, list):
                results = [results]

            min_conf = float(getattr(settings, "EMOTION_MIN_FACE_CONFIDENCE", 0.5))
            frame_area = float(frame.shape[0] * frame.shape[1]) if frame is not None else 0.0

            emotions = []
            for face in results:
                # Reject non-faces. With enforce_detection=False DeepFace returns a
                # result for every frame: when it finds nothing it hands back the
                # WHOLE FRAME as the "face" region, face_confidence 0.0 and no eye
                # landmarks — then classifies that. Those reads look confident and
                # are pure noise, so they must not reach the sentiment aggregate.
                if float(face.get("face_confidence") or 0.0) < min_conf:
                    continue
                region = face.get("region") or {}
                if region.get("left_eye") is None and region.get("right_eye") is None:
                    continue
                if frame_area:
                    region_area = float(region.get("w", 0)) * float(region.get("h", 0))
                    if region_area >= 0.9 * frame_area:
                        continue

                emotion_scores = face.get("emotion", {})
                dominant = face.get("dominant_emotion", "neutral")
                result = {
                    "dominant_emotion": dominant,
                    "confidence": emotion_scores.get(dominant, 0.0) / 100.0,
                    "all_emotions": {k: round(v / 100.0, 3) for k, v in emotion_scores.items()},
                    "camera_id": camera_id,
                    "zone": zone,
                    "face_confidence": round(float(face.get("face_confidence") or 0.0), 3),
                    "bbox": [region.get("x", 0), region.get("y", 0),
                             region.get("w", 0), region.get("h", 0)],
                }
                emotions.append(result)

                if zone:
                    self.zone_aggregation[zone].append(result)
                    if len(self.zone_aggregation[zone]) > 500:
                        self.zone_aggregation[zone] = self.zone_aggregation[zone][-250:]

            return emotions

        except Exception as e:
            logger.error(f"Emotion analysis failed: {e}")
            return []

    def analyze_frame_summary(
        self,
        frame: np.ndarray,
        camera_id: int = 0,
        zone: str = None,
    ) -> Dict[str, Any]:
        """
        Per-frame summary aggregating all detected faces.
        Returns a single dict (dominant emotion, distribution, sentiment_score)
        rather than a list — convenient for pipeline aggregation.
        """
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
        for f in faces:
            emotion_counts[f.get("dominant_emotion", "neutral")] += 1
        total = sum(emotion_counts.values())
        dominant = max(emotion_counts, key=emotion_counts.get)
        distribution = {k: round(v / total, 3) for k, v in emotion_counts.items()}
        positive = emotion_counts.get("happy", 0) + emotion_counts.get("surprise", 0)
        negative = (
            emotion_counts.get("sad", 0)
            + emotion_counts.get("angry", 0)
            + emotion_counts.get("fear", 0)
            + emotion_counts.get("disgust", 0)
        )
        sentiment = (positive - negative) / max(total, 1)
        return {
            "camera_id": camera_id,
            "zone": zone,
            "sample_count": total,
            "dominant_emotion": dominant,
            "emotion_distribution": distribution,
            "sentiment_score": round(sentiment, 3),
        }

    def analyze_demographics(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Extended analysis: age + gender + emotion (for Store Vibe).
        """
        if not DEEPFACE_AVAILABLE:
            return self._mock_demographics()

        try:
            results = DeepFace.analyze(
                img_path=frame,
                actions=["emotion", "age", "gender"],
                enforce_detection=False,
                detector_backend=self.detector_backend,
                silent=True,
            )
            if not isinstance(results, list):
                results = [results]

            demographics = []
            for face in results:
                demographics.append({
                    "estimated_age": face.get("age", None),
                    "estimated_gender": face.get("dominant_gender", None),
                    "gender_confidence": max(face.get("gender", {}).values()) / 100.0 if face.get("gender") else None,
                    "dominant_emotion": face.get("dominant_emotion", "neutral"),
                    "emotion_scores": {k: round(v / 100.0, 3) for k, v in face.get("emotion", {}).items()},
                })
            return demographics
        except Exception as e:
            logger.error(f"Demographic analysis failed: {e}")
            return []

    def get_zone_summary(self, zone: str) -> Dict[str, Any]:
        """Get aggregated emotion summary for a zone."""
        entries = self.zone_aggregation.get(zone, [])
        if not entries:
            return {"zone": zone, "sample_count": 0, "sentiment_score": 0.0}

        emotion_counts = defaultdict(int)
        for e in entries:
            emotion_counts[e["dominant_emotion"]] += 1

        total = len(entries)
        distribution = {k: round(v / total, 3) for k, v in emotion_counts.items()}
        dominant = max(emotion_counts, key=emotion_counts.get)

        # Sentiment score: -1 (negative) to +1 (positive)
        positive = emotion_counts.get("happy", 0) + emotion_counts.get("surprise", 0)
        negative = emotion_counts.get("sad", 0) + emotion_counts.get("angry", 0) + emotion_counts.get("fear", 0) + emotion_counts.get("disgust", 0)
        sentiment = (positive - negative) / max(total, 1)

        return {
            "zone": zone,
            "dominant_emotion": dominant,
            "emotion_distribution": distribution,
            "sample_count": total,
            "sentiment_score": round(sentiment, 3),
        }

    def get_store_sentiment(self) -> Dict[str, Any]:
        """Aggregate sentiment across all zones — feeds Store Vibe Score."""
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

    def _mock_analyze(self, camera_id: int, zone: str = None) -> List[Dict[str, Any]]:
        emotions = np.random.dirichlet(np.ones(7))
        emotion_map = dict(zip(self.EMOTIONS, [round(float(e), 3) for e in emotions]))
        dominant = max(emotion_map, key=emotion_map.get)
        return [{
            "dominant_emotion": dominant,
            "confidence": emotion_map[dominant],
            "all_emotions": emotion_map,
            "camera_id": camera_id,
            "zone": zone,
        }]

    def _mock_demographics(self) -> List[Dict[str, Any]]:
        return [{
            "estimated_age": float(np.random.randint(18, 65)),
            "estimated_gender": np.random.choice(["Man", "Woman"]),
            "gender_confidence": round(float(np.random.uniform(0.7, 0.99)), 3),
            "dominant_emotion": np.random.choice(self.EMOTIONS),
            "emotion_scores": dict(zip(self.EMOTIONS, [round(float(x), 3) for x in np.random.dirichlet(np.ones(7))])),
        }]

    def reset(self):
        self.zone_aggregation.clear()
