"""
OmniTrack AI — Crowd Density Estimator
Zone-based person counting and density classification.
"""

import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger


@dataclass
class CrowdZone:
    zone_name: str
    camera_id: int
    area_sqm: float = 50.0
    threshold_low: int = 3
    threshold_medium: int = 8
    threshold_high: int = 15
    threshold_critical: int = 25
    bbox: Optional[List[float]] = None  # [x1, y1, x2, y2] in frame coords
    max_capacity: int = 50


class CrowdDensityEstimator:
    """
    Zone-based crowd density estimation.
    Counts persons per zone, classifies density level, tracks trends.
    """

    def __init__(self, zones: Optional[List[CrowdZone]] = None):
        self.zones: List[CrowdZone] = zones or []
        self.history: Dict[str, List[Dict]] = defaultdict(list)
        # camera_id -> zone_name (pipeline registers one zone per camera at add_camera time)
        self._camera_zone: Dict[int, str] = {}

    def add_zone(self, zone: CrowdZone):
        self.zones.append(zone)
        self._camera_zone[zone.camera_id] = zone.zone_name

    def configure_zone(
        self,
        zone_name: str,
        camera_id: int = 0,
        max_capacity: int = 50,
        area_sqm: float = 50.0,
        bbox: Optional[List[float]] = None,
    ) -> CrowdZone:
        """
        Register (or update) a named zone for a camera using sensible defaults.
        Thresholds scale with max_capacity so a 200-capacity zone doesn't
        flag 'critical' at 25 people.
        """
        existing = next((z for z in self.zones if z.zone_name == zone_name), None)
        if existing:
            existing.camera_id = camera_id
            existing.max_capacity = max_capacity
            existing.area_sqm = area_sqm
            if bbox is not None:
                existing.bbox = bbox
            self._camera_zone[camera_id] = zone_name
            return existing
        cap = max(max_capacity, 1)
        zone = CrowdZone(
            zone_name=zone_name,
            camera_id=camera_id,
            area_sqm=area_sqm,
            threshold_low=max(1, int(cap * 0.10)),
            threshold_medium=max(2, int(cap * 0.30)),
            threshold_high=max(3, int(cap * 0.60)),
            threshold_critical=max(4, int(cap * 0.90)),
            bbox=bbox,
            max_capacity=cap,
        )
        self.add_zone(zone)
        return zone

    def update(
        self,
        camera_id: int,
        detections: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Per-camera update called by the processing pipeline.
        Returns a single zone status dict (or an empty-zone fallback) for
        the zone registered against this camera.
        """
        zone = next((z for z in self.zones if z.camera_id == camera_id), None)
        if zone is None:
            return {
                "zone": f"cam-{camera_id}",
                "camera_id": camera_id,
                "person_count": len(detections),
                "density": 0.0,
                "classification": "empty",
                "area_sqm": 0.0,
            }
        count = self._count_in_zone(detections, zone)
        density = count / max(zone.area_sqm, 1)
        classification = self._classify(count, zone)
        status = {
            "zone": zone.zone_name,
            "camera_id": camera_id,
            "person_count": count,
            "density": round(density, 4),
            "classification": classification,
            "area_sqm": zone.area_sqm,
            "max_capacity": zone.max_capacity,
        }
        self.history[zone.zone_name].append({
            "count": count,
            "classification": classification,
        })
        if len(self.history[zone.zone_name]) > 1000:
            self.history[zone.zone_name] = self.history[zone.zone_name][-500:]
        return status

    def estimate(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Estimate crowd density per zone given current detections.
        detections: [{"bbox": [x,y,w,h], "confidence": float}, ...]
        Returns per-zone status.
        """
        results = []

        for zone in self.zones:
            count = self._count_in_zone(detections, zone)
            density = count / max(zone.area_sqm, 1)
            classification = self._classify(count, zone)

            status = {
                "zone": zone.zone_name,
                "camera_id": zone.camera_id,
                "person_count": count,
                "density": round(density, 4),
                "classification": classification,
                "area_sqm": zone.area_sqm,
            }
            results.append(status)

            # Track history
            self.history[zone.zone_name].append({
                "count": count,
                "classification": classification,
            })
            # Keep last 1000
            if len(self.history[zone.zone_name]) > 1000:
                self.history[zone.zone_name] = self.history[zone.zone_name][-500:]

        return results

    def _count_in_zone(self, detections: List[Dict], zone: CrowdZone) -> int:
        """Count persons inside a zone bounding box."""
        if zone.bbox is None:
            return len(detections)  # No bbox = whole frame

        zx1, zy1, zx2, zy2 = zone.bbox
        count = 0
        for det in detections:
            bx, by, bw, bh = det["bbox"]
            cx, cy = bx + bw / 2, by + bh / 2
            if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                count += 1
        return count

    def _classify(self, count: int, zone: CrowdZone) -> str:
        if count >= zone.threshold_critical:
            return "critical"
        elif count >= zone.threshold_high:
            return "high"
        elif count >= zone.threshold_medium:
            return "medium"
        elif count >= zone.threshold_low:
            return "low"
        return "empty"

    def get_zone_history(self, zone_name: str, limit: int = 100) -> List[Dict]:
        return self.history.get(zone_name, [])[-limit:]

    def get_all_status(self, detections: List[Dict]) -> Dict[str, Any]:
        """Get comprehensive crowd status across all zones."""
        statuses = self.estimate(detections)
        total = sum(s["person_count"] for s in statuses)
        critical_zones = [s for s in statuses if s["classification"] == "critical"]
        return {
            "total_occupancy": total,
            "zones": statuses,
            "critical_zones": len(critical_zones),
            "critical_zone_names": [z["zone"] for z in critical_zones],
        }

    def reset(self):
        self.history.clear()
