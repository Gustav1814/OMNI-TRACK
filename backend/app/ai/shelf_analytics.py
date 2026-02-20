"""
OmniTrack AI — Shelf Engagement Analytics
Zone-based dwell-time tracking for product shelf interaction analysis.
Identifies top-selling zones by customer engagement levels.
"""

import time
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger


@dataclass
class ShelfZoneConfig:
    zone_id: str
    zone_name: str
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    camera_id: int


@dataclass
class PersonDwell:
    track_id: int
    zone_id: str
    enter_time: float
    last_seen: float
    total_time: float = 0.0


class ShelfAnalytics:
    """
    Shelf engagement analytics engine.
    Tracks person dwell-time per defined shelf zone ROI.
    """

    def __init__(self, zones: Optional[List[ShelfZoneConfig]] = None):
        self.zones: List[ShelfZoneConfig] = zones or []
        self.active_dwells: Dict[str, PersonDwell] = {}  # key: "trackid_zoneid"
        self.zone_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"visit_count": 0, "total_dwell_time": 0.0, "visitors": set()}
        )

    def add_zone(self, zone: ShelfZoneConfig):
        self.zones.append(zone)

    def update(self, tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Update shelf analytics with current track positions.
        tracks: [{"track_id": int, "bbox": [x,y,w,h], "confidence": float}, ...]
        Returns list of active zone engagements.
        """
        now = time.time()
        current_in_zone = set()

        engagements = []
        for track in tracks:
            tid = track["track_id"]
            tx, ty, tw, th = track["bbox"]
            person_center = (tx + tw / 2, ty + th / 2)

            for zone in self.zones:
                zx1, zy1, zx2, zy2 = zone.bbox
                key = f"{tid}_{zone.zone_id}"

                if zx1 <= person_center[0] <= zx2 and zy1 <= person_center[1] <= zy2:
                    current_in_zone.add(key)
                    if key not in self.active_dwells:
                        self.active_dwells[key] = PersonDwell(
                            track_id=tid, zone_id=zone.zone_id,
                            enter_time=now, last_seen=now
                        )
                    else:
                        self.active_dwells[key].last_seen = now
                        self.active_dwells[key].total_time = now - self.active_dwells[key].enter_time

                    engagements.append({
                        "track_id": tid,
                        "zone_id": zone.zone_id,
                        "zone_name": zone.zone_name,
                        "dwell_time": self.active_dwells[key].total_time,
                    })

        # Finalize exited dwells
        exited = set(self.active_dwells.keys()) - current_in_zone
        for key in exited:
            dwell = self.active_dwells.pop(key)
            stats = self.zone_stats[dwell.zone_id]
            stats["visit_count"] += 1
            stats["total_dwell_time"] += dwell.total_time
            stats["visitors"].add(dwell.track_id)

        return engagements

    def get_zone_rankings(self) -> List[Dict[str, Any]]:
        """Get zones ranked by engagement score."""
        rankings = []
        for zone in self.zones:
            stats = self.zone_stats[zone.zone_id]
            visits = stats["visit_count"]
            total_time = stats["total_dwell_time"]
            avg_dwell = total_time / max(visits, 1)
            # Engagement score: weighted combination of visits and dwell time
            score = (visits * 0.4 + avg_dwell * 0.6) * 10
            rankings.append({
                "zone_id": zone.zone_id,
                "zone_name": zone.zone_name,
                "visit_count": visits,
                "avg_dwell_time": round(avg_dwell, 2),
                "engagement_score": round(score, 2),
                "unique_visitors": len(stats["visitors"]),
            })
        rankings.sort(key=lambda x: x["engagement_score"], reverse=True)
        for i, r in enumerate(rankings):
            r["rank"] = i + 1
        return rankings

    def get_top_zones(self, n: int = 5) -> List[Dict[str, Any]]:
        return self.get_zone_rankings()[:n]

    def reset(self):
        self.active_dwells.clear()
        self.zone_stats.clear()
