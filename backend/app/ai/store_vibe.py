"""
OmniTrack AI — Store Vibe Engine
Aggregates sentiment, crowd energy, shelf engagement, and foot traffic
into a unified "Store Vibe Score" (0–100).
"""

import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from loguru import logger


@dataclass
class VibeSnapshot:
    overall_score: float
    sentiment_score: float
    energy_score: float
    engagement_score: float
    foot_traffic_score: float
    label: str
    timestamp: float


class StoreVibeEngine:
    """
    Computes a real-time Store Vibe Score by combining:
    - Sentiment: emotion recognition aggregate
    - Energy: crowd density + movement velocity
    - Engagement: shelf dwell-time averages
    - Foot Traffic: visitor count vs capacity
    """

    VIBE_LABELS = {
        (0, 20): "Quiet",
        (20, 40): "Calm",
        (40, 60): "Steady",
        (60, 80): "Energetic",
        (80, 101): "Buzzing",
    }

    def __init__(
        self,
        store_capacity: int = 200,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.store_capacity = store_capacity
        self.weights = weights or {
            "sentiment": 0.25,
            "energy": 0.25,
            "engagement": 0.25,
            "foot_traffic": 0.25,
        }
        self.history: List[VibeSnapshot] = []

    def compute(
        self,
        sentiment_score: float = 0.0,   # -1 to +1 from emotion module
        crowd_count: int = 0,
        crowd_classification: str = "empty",
        avg_dwell_time: float = 0.0,    # seconds, from shelf analytics
        visitor_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Compute the Store Vibe Score.
        Returns dict with overall score, component scores, and vibe label.
        """
        # Normalize sentiment: -1..+1 → 0..100
        sentiment_normalized = (sentiment_score + 1) / 2 * 100

        # Energy score: based on crowd classification
        energy_map = {"empty": 10, "low": 30, "medium": 50, "high": 75, "critical": 95}
        energy = energy_map.get(crowd_classification, 50)

        # Engagement: dwell time normalized (0-300s → 0-100)
        engagement = min(avg_dwell_time / 300 * 100, 100)

        # Foot traffic: visitor count vs capacity
        traffic = min(visitor_count / max(self.store_capacity, 1) * 100, 100)

        # Weighted composite
        overall = (
            self.weights["sentiment"] * sentiment_normalized +
            self.weights["energy"] * energy +
            self.weights["engagement"] * engagement +
            self.weights["foot_traffic"] * traffic
        )
        overall = round(max(0, min(100, overall)), 1)

        label = self._get_label(overall)

        snapshot = VibeSnapshot(
            overall_score=overall,
            sentiment_score=round(sentiment_normalized, 1),
            energy_score=round(energy, 1),
            engagement_score=round(engagement, 1),
            foot_traffic_score=round(traffic, 1),
            label=label,
            timestamp=time.time(),
        )
        self.history.append(snapshot)
        if len(self.history) > 500:
            self.history = self.history[-250:]

        return {
            "overall_score": snapshot.overall_score,
            "sentiment_score": snapshot.sentiment_score,
            "energy_score": snapshot.energy_score,
            "engagement_score": snapshot.engagement_score,
            "foot_traffic_score": snapshot.foot_traffic_score,
            "vibe_label": label,
            "timestamp": snapshot.timestamp,
        }

    def calculate(
        self,
        sentiment_score: float = 0.0,
        crowd_count: int = 0,
        max_capacity: Optional[int] = None,
        engagement_score: float = 0.0,
        foot_traffic: int = 0,
    ) -> Dict[str, Any]:
        """
        Pipeline-friendly wrapper.
        - sentiment_score: -1..+1 from emotion aggregator
        - crowd_count: current occupancy across all cameras
        - max_capacity: optional per-call capacity override (else uses store_capacity)
        - engagement_score: already-normalized 0..100 score from shelf analytics aggregate
        - foot_traffic: visitor count this tick (used against capacity)
        """
        cap = max_capacity if max_capacity is not None else self.store_capacity
        sentiment_normalized = (max(-1.0, min(1.0, sentiment_score)) + 1) / 2 * 100
        occupancy_ratio = min(crowd_count / max(cap, 1), 1.0)
        energy = occupancy_ratio * 100
        engagement = max(0.0, min(100.0, float(engagement_score)))
        traffic = min(foot_traffic / max(cap, 1) * 100, 100)

        overall = (
            self.weights["sentiment"] * sentiment_normalized +
            self.weights["energy"] * energy +
            self.weights["engagement"] * engagement +
            self.weights["foot_traffic"] * traffic
        )
        overall = round(max(0, min(100, overall)), 1)
        label = self._get_label(overall)

        snapshot = VibeSnapshot(
            overall_score=overall,
            sentiment_score=round(sentiment_normalized, 1),
            energy_score=round(energy, 1),
            engagement_score=round(engagement, 1),
            foot_traffic_score=round(traffic, 1),
            label=label,
            timestamp=time.time(),
        )
        self.history.append(snapshot)
        if len(self.history) > 500:
            self.history = self.history[-250:]

        return {
            "overall_score": snapshot.overall_score,
            "sentiment_score": snapshot.sentiment_score,
            "energy_score": snapshot.energy_score,
            "engagement_score": snapshot.engagement_score,
            "foot_traffic_score": snapshot.foot_traffic_score,
            "vibe_label": label,
            "timestamp": snapshot.timestamp,
        }

    def get_current(self) -> Optional[Dict[str, Any]]:
        """Latest snapshot as a plain dict (for the vibe router)."""
        if not self.history:
            return None
        s = self.history[-1]
        return {
            "overall_score": s.overall_score,
            "sentiment_score": s.sentiment_score,
            "energy_score": s.energy_score,
            "engagement_score": s.engagement_score,
            "foot_traffic_score": s.foot_traffic_score,
            "vibe_label": s.label,
            "timestamp": s.timestamp,
        }

    def _get_label(self, score: float) -> str:
        for (low, high), label in self.VIBE_LABELS.items():
            if low <= score < high:
                return label
        return "Steady"

    def get_trend(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent vibe trend."""
        return [
            {
                "overall": s.overall_score,
                "label": s.label,
                "timestamp": s.timestamp,
            }
            for s in self.history[-limit:]
        ]

    def reset(self):
        self.history.clear()
