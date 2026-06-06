"""
OmniTrack AI - lightweight emotion summary module.

The module keeps the existing emotion APIs available with OpenCV face
sampling, and uses mock output only when ALLOW_MOCK_AI=true.
"""

from collections import defaultdict
from typing import Any, Dict, List

import cv2
import numpy as np

from app.config import settings


class EmotionRecognizer:
    """
    Lightweight face sampling for sentiment dashboards.

    Without a dedicated emotion classifier, detected faces are reported as
    neutral samples. This avoids pretending to infer affect while preserving
    the dashboard contract.
    """

    EMOTIONS = ["happy", "sad", "angry", "surprise", "neutral", "fear", "disgust"]

    def __init__(self, backend: str = "opencv", detector_backend: str = "opencv"):
        self.backend = backend
        self.detector_backend = detector_backend
        self.zone_aggregation: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.face_cascade = cv2.CascadeClassifier(cascade_path)

    def analyze_frame(
        self, frame: np.ndarray, camera_id: int = 0, zone: str = None
    ) -> List[Dict[str, Any]]:
        """Analyze a frame and return neutral face samples."""
        if settings.ALLOW_MOCK_AI:
            return self._mock_analyze(camera_id, zone)

        if frame is None or self.face_cascade.empty():
            return []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(36, 36),
        )

        emotions: List[Dict[str, Any]] = []
        frame_area = max(float(frame.shape[0] * frame.shape[1]), 1.0)
        for _x, _y, w, h in faces[:8]:
            confidence = min(0.95, max(0.35, float(w * h) / frame_area * 8))
            result = {
                "dominant_emotion": "neutral",
                "confidence": round(confidence, 3),
                "all_emotions": {"neutral": 1.0},
                "camera_id": camera_id,
                "zone": zone,
            }
            emotions.append(result)
            self._remember_zone_sample(zone, result)

        return emotions

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

    def analyze_demographics(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Demographics require a dedicated model; mock only when enabled."""
        if settings.ALLOW_MOCK_AI:
            return self._mock_demographics()
        return []

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
        )
        return (positive - negative) / max(total, 1)

    def _mock_analyze(self, camera_id: int, zone: str = None) -> List[Dict[str, Any]]:
        emotions = np.random.dirichlet(np.ones(7))
        emotion_map = dict(zip(self.EMOTIONS, [round(float(e), 3) for e in emotions]))
        dominant = max(emotion_map, key=emotion_map.get)
        result = {
            "dominant_emotion": dominant,
            "confidence": emotion_map[dominant],
            "all_emotions": emotion_map,
            "camera_id": camera_id,
            "zone": zone,
        }
        self._remember_zone_sample(zone, result)
        return [result]

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
