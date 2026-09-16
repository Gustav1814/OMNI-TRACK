"""
OmniTrack AI — General object detection KPI
═══════════════════════════════════════════════════════════════

Ported from VisRax `services/kpis/kpi_general_object_detection.py`.

The simplest KPI: no regions, no tracking dependency, no cross-frame state.
It logs what was detected in the current frame and keeps rolling per-class
totals. This is the right shape for detector-only models such as fire/smoke,
where "is it present, and when" is the whole question.

VisRax leaves this KPI out of `kpi_region_mappings.py` entirely, so its
`allowed_region_types` is empty — the Camera Marking step is not applicable.
"""

import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from app.ai.regions import GLOBAL, REGION_NAME

# How many recent frames of detections to keep for the live view.
_MAX_RECENT_FRAMES = 60


class GeneralDetectionKPI:
    """Per-camera detection log with rolling class totals."""

    def __init__(self, max_recent: int = _MAX_RECENT_FRAMES) -> None:
        # Per-frame snapshots, newest last.
        self.recent: Deque[Dict[str, Any]] = deque(maxlen=max_recent)
        # Cumulative sightings per class (one per detection per frame).
        self.class_totals: Dict[str, int] = {}
        # Peak simultaneous detections of each class in a single frame.
        self.class_peak: Dict[str, int] = {}
        self.frames_seen: int = 0
        self.last_seen_ts: Optional[float] = None

    def update(self, detections: List[Dict[str, Any]], now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        self.frames_seen += 1

        frame_counts: Dict[str, int] = {}
        records: List[Dict[str, Any]] = []

        for detection in detections:
            class_name = str(detection.get("class_name", "unknown"))
            frame_counts[class_name] = frame_counts.get(class_name, 0) + 1
            records.append({
                "class_name": class_name,
                "confidence": round(float(detection.get("confidence", 0.0)), 4),
                "bbox": detection.get("bbox", []),
                "track_id": detection.get("track_id"),
                "global_id": detection.get("global_id"),
                "region_name": str(detection.get(REGION_NAME, GLOBAL)),
            })

        for class_name, count in frame_counts.items():
            self.class_totals[class_name] = self.class_totals.get(class_name, 0) + count
            if count > self.class_peak.get(class_name, 0):
                self.class_peak[class_name] = count

        if records:
            self.last_seen_ts = now

        self.recent.append({"at": now, "count": len(records), "detections": records})

    # ── reporting ─────────────────────────────────────────────────
    def current_frame(self) -> Dict[str, Any]:
        return self.recent[-1] if self.recent else {"at": None, "count": 0, "detections": []}

    def current_counts(self) -> Dict[str, int]:
        """Per-class counts in the most recent frame."""
        counts: Dict[str, int] = {}
        for record in self.current_frame().get("detections", []):
            name = record["class_name"]
            counts[name] = counts.get(name, 0) + 1
        return counts

    def snapshot(self) -> Dict[str, Any]:
        return {
            "current": self.current_counts(),
            "totals": dict(self.class_totals),
            "peak": dict(self.class_peak),
            "frames_seen": self.frames_seen,
            "last_seen_ts": self.last_seen_ts,
        }

    def recent_as_list(self) -> List[Dict[str, Any]]:
        return list(self.recent)

    def reset(self) -> None:
        self.recent.clear()
        self.class_totals.clear()
        self.class_peak.clear()
        self.frames_seen = 0
        self.last_seen_ts = None
