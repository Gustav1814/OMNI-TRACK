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


def _scalar_int(v: Any) -> Optional[int]:
    """Coerce track ids from int, numpy integers, or numeric strings."""
    if v is None:
        return None
    try:
        if hasattr(v, "item"):
            v = v.item()
        return int(v)
    except (TypeError, ValueError):
        return None


def _normalize_zone_bbox(bbox: Tuple[Any, Any, Any, Any]) -> Tuple[int, int, int, int]:
    """Order as x1,y1,x2,y2 with x1<=x2, y1<=y2 so hit-tests never break on drag-inverted rects."""
    x1, y1, x2, y2 = (float(bbox[i]) for i in range(4))
    if not all(map(lambda t: t == t, (x1, y1, x2, y2))):  # NaN check
        raise ValueError("Shelf zone bbox contains NaN")
    xa, xb = sorted((x1, x2))
    ya, yb = sorted((y1, y2))
    return int(xa), int(ya), int(xb), int(yb)


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
        try:
            bbox = _normalize_zone_bbox(zone.bbox)
        except (TypeError, ValueError) as e:
            logger.warning(f"[ShelfAnalytics] Ignoring invalid zone bbox {zone.bbox!r}: {e}")
            return
        self.zones.append(
            ShelfZoneConfig(
                zone_id=str(zone.zone_id),
                zone_name=str(zone.zone_name),
                bbox=bbox,
                camera_id=int(zone.camera_id),
            )
        )

    def update(
        self,
        *args,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Flexible update entrypoint.
        Supported call shapes:
          - update(tracks)                       (legacy)
          - update(camera_id, tracks)            (pipeline)
          - update(camera_id, tracks, timestamp) (pipeline)
        Returns a camera-scoped summary dict (per-camera engagement snapshot).
        """
        camera_id: Optional[int] = None
        tracks: List[Dict[str, Any]] = []
        if kwargs:
            camera_id = kwargs.get("camera_id")
            tracks = kwargs.get("tracks") or kwargs.get("detections") or []
        elif len(args) == 1:
            tracks = args[0] or []
        elif len(args) == 2:
            camera_id, tracks = args[0], args[1] or []
        elif len(args) >= 3:
            camera_id, tracks = args[0], args[1] or []
        engagements = self._update_tracks(tracks, camera_id=camera_id)
        avg_engagement = (
            sum(float(e.get("dwell_time", 0) or 0) for e in engagements) / len(engagements)
            if engagements else 0.0
        )
        safe_engagements = []
        for e in engagements:
            stid = _scalar_int(e.get("track_id"))
            if stid is None:
                continue
            safe_engagements.append({
                "track_id": stid,
                "zone_id": str(e.get("zone_id", "")),
                "zone_name": str(e.get("zone_name", "")),
                "dwell_time": round(float(e.get("dwell_time", 0) or 0), 3),
            })
        return {
            "camera_id": int(camera_id) if camera_id is not None else None,
            "active_engagements": safe_engagements,
            "engagement_score": round(min(float(avg_engagement) / 300 * 100, 100), 2),
            "total_active": len(safe_engagements),
        }

    def _update_tracks(
        self,
        tracks: List[Dict[str, Any]],
        camera_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Update shelf analytics with current track positions.
        tracks: [{"track_id": int, "bbox": [x,y,w,h], "confidence": float}, ...]
        Returns list of active zone engagements.
        """
        now = time.time()
        current_in_zone = set()

        engagements = []
        for track in tracks:
            tid = _scalar_int(track.get("track_id"))
            bbox = track.get("bbox") or track.get("box") or []
            if tid is None or len(bbox) < 4:
                continue
            try:
                tx = float(bbox[0])
                ty = float(bbox[1])
                tw = float(bbox[2])
                th = float(bbox[3])
            except (TypeError, ValueError):
                continue
            if tw <= 0 or th <= 0 or not (tw == tw and th == th):
                continue
            person_center = (tx + tw / 2, ty + th / 2)

            zones_for_cam = [
                z for z in self.zones
                if camera_id is None or z.camera_id == camera_id
            ]
            for zone in zones_for_cam:
                try:
                    zx1, zy1, zx2, zy2 = _normalize_zone_bbox(zone.bbox)
                except (TypeError, ValueError):
                    continue
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
                "zone_id": str(zone.zone_id),
                "zone_name": str(zone.zone_name),
                "visit_count": int(visits),
                "avg_dwell_time": round(float(avg_dwell), 2),
                "engagement_score": round(float(score), 2),
                "unique_visitors": int(len(stats["visitors"])),
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


# Back-compat alias — the pipeline imports `ShelfEngagementTracker`.
ShelfEngagementTracker = ShelfAnalytics
