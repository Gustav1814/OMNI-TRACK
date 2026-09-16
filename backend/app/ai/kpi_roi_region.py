"""
OmniTrack AI — ROI region dependency KPI
═══════════════════════════════════════════════════════════════

Ported from VisRax `services/kpis/kpi_roi_region_dependency_object_detection.py`.

Tracks, per object, which regions it visited in order and how long it spent in
each. Geometry is done in `app.ai.regions`; this only accumulates dwell.

Durations are kept in MICROSECONDS to match VisRax's `region_with_time`.
Consecutive entries for the same region are merged, so a track that flickers
between "zone-a" and "zone-a" does not produce a long visit list.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.ai.regions import GLOBAL, ITEM_AT_CURRENT_REGION_NAME


@dataclass
class TrackROIDwell:
    """One tracked object's region history. Mirrors VisRax's ROICountTime."""

    track_id: int
    class_name: str = "unknown"
    global_id: Optional[str] = None
    first_seen_in_region: str = GLOBAL
    last_seen_in_region: str = GLOBAL
    region_name: str = GLOBAL
    visited_regions: List[str] = field(default_factory=list)
    region_dwell_us: List[int] = field(default_factory=list)
    current_region_dwell_us: int = 0
    first_seen_ts: float = field(default_factory=time.time)
    last_seen_ts: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "class_name": self.class_name,
            "global_id": self.global_id,
            "first_seen_in_region": self.first_seen_in_region,
            "last_seen_in_region": self.last_seen_in_region,
            "region_name": self.region_name,
            "visited_regions": list(self.visited_regions),
            "region_dwell_us": list(self.region_dwell_us),
            "current_region_dwell_us": self.current_region_dwell_us,
            "first_seen_ts": self.first_seen_ts,
            "last_seen_ts": self.last_seen_ts,
        }


class ROIRegionKPI:
    """Per-camera ROI dwell accumulator."""

    def __init__(self) -> None:
        self.tracks: Dict[int, TrackROIDwell] = {}

    def update(self, detections: List[Dict[str, Any]], now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()

        for detection in detections:
            track_id = detection.get("track_id")
            if track_id is None:
                continue
            try:
                track_id = int(track_id)
            except (TypeError, ValueError):
                continue

            current_region = str(detection.get(ITEM_AT_CURRENT_REGION_NAME, GLOBAL))
            track = self.tracks.get(track_id)

            if track is None:
                self.tracks[track_id] = TrackROIDwell(
                    track_id=track_id,
                    class_name=str(detection.get("class_name", "unknown")),
                    global_id=detection.get("global_id"),
                    first_seen_in_region=current_region,
                    last_seen_in_region=current_region,
                    region_name=current_region,
                    visited_regions=[current_region] if current_region else [],
                    region_dwell_us=[0] if current_region else [],
                    first_seen_ts=now,
                    last_seen_ts=now,
                )
                continue

            self._update_existing(track, current_region, now)

    def _update_existing(self, track: TrackROIDwell, current_region: str, now: float) -> None:
        delta_us = int(max(0.0, now - track.last_seen_ts) * 1_000_000)

        regions = track.visited_regions
        durations = track.region_dwell_us

        if not regions and current_region:
            regions.append(current_region)
            durations.append(0)

        # Time since the last frame belongs to the region the track was in then.
        if durations:
            durations[-1] += delta_us

        # Entering a different region opens a new entry.
        if current_region and (not regions or current_region != regions[-1]):
            regions.append(current_region)
            durations.append(0)

        merged_regions, merged_durations = self._merge_consecutive(regions, durations)
        track.visited_regions = merged_regions
        track.region_dwell_us = merged_durations
        track.current_region_dwell_us = merged_durations[-1] if merged_durations else 0
        track.last_seen_ts = now
        track.last_seen_in_region = current_region
        track.region_name = current_region

    @staticmethod
    def _merge_consecutive(
        regions: List[str], durations: List[int]
    ) -> tuple[List[str], List[int]]:
        merged_regions: List[str] = []
        merged_durations: List[int] = []
        for reg, dur in zip(regions, durations):
            if merged_regions and reg == merged_regions[-1]:
                merged_durations[-1] += dur
            else:
                merged_regions.append(reg)
                merged_durations.append(dur)
        return merged_regions, merged_durations

    # ── reporting ─────────────────────────────────────────────────
    def totals_by_region(self) -> Dict[str, Dict[str, Any]]:
        """
        Per-region visit counts and dwell, mirroring VisRax's get_roi_summary:
        visits, unique tracks, total and average dwell seconds.
        """
        totals: Dict[str, Dict[str, Any]] = {}
        for track in self.tracks.values():
            for region, dwell_us in zip(track.visited_regions, track.region_dwell_us):
                if not region or region == GLOBAL:
                    continue
                bucket = totals.setdefault(
                    region,
                    {"visits": 0, "unique_tracks": set(), "total_dwell_us": 0},
                )
                bucket["visits"] += 1
                bucket["unique_tracks"].add(track.track_id)
                bucket["total_dwell_us"] += dwell_us

        out: Dict[str, Dict[str, Any]] = {}
        for region, bucket in totals.items():
            unique = len(bucket["unique_tracks"])
            total_s = bucket["total_dwell_us"] / 1_000_000
            out[region] = {
                "visits": bucket["visits"],
                "unique_tracks": unique,
                "total_dwell_seconds": round(total_s, 2),
                "avg_dwell_seconds": round(total_s / unique, 2) if unique else 0.0,
            }
        return out

    def current_occupancy(self) -> Dict[str, int]:
        """How many tracks are in each region right now."""
        occupancy: Dict[str, int] = {}
        for track in self.tracks.values():
            region = track.last_seen_in_region
            if region and region != GLOBAL:
                occupancy[region] = occupancy.get(region, 0) + 1
        return occupancy

    def snapshot(self) -> Dict[str, Any]:
        return {
            "by_region": self.totals_by_region(),
            "occupancy": self.current_occupancy(),
            "tracked": len(self.tracks),
        }

    def tracks_as_list(self) -> List[Dict[str, Any]]:
        return [t.as_dict() for t in self.tracks.values()]

    def reset(self) -> None:
        self.tracks.clear()
