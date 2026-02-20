"""
OmniTrack AI — Emotion Recognition Module
DeepFace/FER-based facial emotion classification.
7-class: happy, sad, angry, surprise, neutral, fear, disgust.
"""

import numpy as np
from typing import List, Dict, Any, Optional
from collections import defaultdict
from loguru import logger

try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except ImportError:
    DEEPFACE_AVAILABLE = False
    logger.warning("DeepFace not installed. Emotion module will run in mock mode.")


class EmotionRecognizer:
    """
    Emotion recognition using DeepFace/FER.
    Detects faces, classifies emotions, aggregates per zone.
    """

    EMOTIONS = ["happy", "sad", "angry", "surprise", "neutral", "fear", "disgust"]

    def __init__(self, backend: str = "opencv", detector_backend: str = "opencv"):
        self.backend = backend
        self.detector_backend = detector_backend
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

            emotions = []
            for face in results:
                emotion_scores = face.get("emotion", {})
                dominant = face.get("dominant_emotion", "neutral")
                result = {
                    "dominant_emotion": dominant,
                    "confidence": emotion_scores.get(dominant, 0.0) / 100.0,
                    "all_emotions": {k: round(v / 100.0, 3) for k, v in emotion_scores.items()},
                    "camera_id": camera_id,
                    "zone": zone,
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
