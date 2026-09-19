"""
OmniTrack AI — Emotion + Demographics Module
DeepFace/FER-based facial analysis.

Emotion is 7-class: happy, sad, angry, surprise, neutral, fear, disgust.
Age and gender ride on the SAME DeepFace call — detection and alignment
dominate the cost, so the extra heads are comparatively cheap and a second
pass would double the most expensive thing in the pipeline.

Every path here returns nothing rather than something invented. The module
previously fell back to randomly generated faces when DeepFace was missing,
which made an absent dependency look like a working sentiment feed.
"""

import sys

import numpy as np
from typing import List, Dict, Any, Optional
from collections import defaultdict
from loguru import logger

from app.config import settings, age_group, normalize_gender

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
        "Emotion and demographics will report no faces."
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

    @staticmethod
    def _usable_face(face: Dict[str, Any], frame_area: float) -> bool:
        """
        Reject non-faces. With enforce_detection=False DeepFace returns a
        result for EVERY frame: when it finds nothing it hands back the whole
        frame as the "face" region, face_confidence 0.0 and no eye landmarks —
        then classifies that. Those reads look confident and are pure noise.

        Shared by the emotion and the demographics path. The demographics path
        had none of these checks and would have recorded the whole-frame
        artefact as a confident thirty-year-old.
        """
        min_conf = float(getattr(settings, "EMOTION_MIN_FACE_CONFIDENCE", 0.5))
        if float(face.get("face_confidence") or 0.0) < min_conf:
            return False
        region = face.get("region") or {}
        if region.get("left_eye") is None and region.get("right_eye") is None:
            return False
        if frame_area:
            region_area = float(region.get("w", 0)) * float(region.get("h", 0))
            if region_area >= 0.9 * frame_area:
                return False
        return True

    def analyze_faces(
        self,
        frame: np.ndarray,
        camera_id: int = 0,
        zone: str = None,
        demographics: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        One DeepFace pass over a frame → one record per usable face.

        With `demographics=True` the same call also asks for age and gender,
        and each record carries `region` so the caller can tie the face to a
        tracked person. Without it, behaviour is exactly the old emotion path.
        """
        if not DEEPFACE_AVAILABLE or frame is None:
            return []

        actions = ["emotion", "age", "gender"] if demographics else ["emotion"]
        try:
            results = DeepFace.analyze(
                img_path=frame,
                actions=actions,
                enforce_detection=False,
                detector_backend=self.detector_backend,
                silent=True,
            )

            if not isinstance(results, list):
                results = [results]

            frame_area = float(frame.shape[0] * frame.shape[1])
            min_px = int(getattr(settings, "DEMOGRAPHICS_MIN_FACE_PX", 60))

            faces = []
            for face in results:
                if not self._usable_face(face, frame_area):
                    continue

                region = face.get("region") or {}
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

                if demographics:
                    # Size gate is demographics-only: emotion off a small face is
                    # coarse but usable, an age off one is a number with no
                    # information in it.
                    w = float(region.get("w", 0) or 0)
                    h = float(region.get("h", 0) or 0)
                    big_enough = min(w, h) >= min_px
                    gender_scores = face.get("gender") or {}
                    result.update({
                        "estimated_age": float(face["age"]) if big_enough and face.get("age") is not None else None,
                        "gender": normalize_gender(face.get("dominant_gender")) if big_enough else "unknown",
                        "gender_confidence": (
                            round(max(gender_scores.values()) / 100.0, 3)
                            if big_enough and gender_scores else None
                        ),
                        "face_px": round(min(w, h), 1),
                        "too_small": not big_enough,
                    })

                faces.append(result)

                if zone:
                    self.zone_aggregation[zone].append(result)
                    if len(self.zone_aggregation[zone]) > 500:
                        self.zone_aggregation[zone] = self.zone_aggregation[zone][-250:]

            return faces

        except Exception as e:
            logger.error(f"Face analysis failed: {e}")
            return []

    def analyze_frame(
        self, frame: np.ndarray, camera_id: int = 0, zone: str = None
    ) -> List[Dict[str, Any]]:
        """Emotion only — one record per detected face."""
        return self.analyze_faces(frame, camera_id=camera_id, zone=zone)

    def analyze_frame_summary(
        self,
        frame: np.ndarray,
        camera_id: int = 0,
        zone: str = None,
        demographics: bool = False,
    ) -> Dict[str, Any]:
        """
        Per-frame summary aggregating all detected faces.
        Returns a single dict (dominant emotion, distribution, sentiment_score)
        rather than a list — convenient for pipeline aggregation.

        `faces` carries the per-face records through so the pipeline can attribute
        each one to a person track; it is stripped before the summary is published.
        """
        faces = self.analyze_faces(
            frame, camera_id=camera_id, zone=zone, demographics=demographics
        )
        if not faces:
            return {
                "camera_id": camera_id,
                "zone": zone,
                "sample_count": 0,
                "dominant_emotion": None,
                "emotion_distribution": {},
                "sentiment_score": 0.0,
                "faces": [],
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
            "faces": faces,
        }

    def analyze_demographics(
        self, frame: np.ndarray, camera_id: int = 0, zone: str = None
    ) -> List[Dict[str, Any]]:
        """
        Age + gender + emotion per face, with the same quality gates the
        emotion path applies plus a minimum face size.
        """
        faces = self.analyze_faces(
            frame, camera_id=camera_id, zone=zone, demographics=True
        )
        return [
            {
                "estimated_age": f.get("estimated_age"),
                "age_group": age_group(f.get("estimated_age")),
                "gender": f.get("gender"),
                "gender_confidence": f.get("gender_confidence"),
                "dominant_emotion": f.get("dominant_emotion"),
                "emotion_scores": f.get("all_emotions"),
                "bbox": f.get("bbox"),
            }
            for f in faces
        ]

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

    # Two mock generators used to live here, returning random emotions and a
    # coin-flip gender whenever DeepFace failed to import. A missing dependency
    # then looked exactly like a working sentiment feed. Both are gone: no
    # faces means no records.

    def reset(self):
        self.zone_aggregation.clear()
