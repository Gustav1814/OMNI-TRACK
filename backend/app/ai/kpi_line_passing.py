"""
OmniTrack AI — Line passing count KPI
═══════════════════════════════════════════════════════════════

Ported from VisRax `services/kpis/kpi_baseline_crossing_kpi.py`.

The geometry lives in `app.ai.regions`; this module only runs the state
machine over per-track records that persist across frames.

COUNTING RULE (VisRax, unchanged) — a crossing is counted exactly once when
all three hold:

  1. the detection is currently flagged as having crossed, and
  2. it is not already counted for that same region
     (`prev_is_crossed and prev_region == current_region` → skip), and
  3. the track was FIRST seen in "global" and is now in a line region

Rule 3 is what stops objects that appear already past the line from being
counted; rule 2 is what stops a stationary object being recounted every frame.

DIRECTION → COUNTER is taken from the line's configuration, not from observed
motion. An `up_to_down` line therefore only ever increments `in_count`. Drawing
a second line with `down_to_up` is how the opposite direction is counted.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.ai.regions import (
    DOWN_TO_UP,
    GLOBAL,
    IS_REGION_CROSSED,
    ITEM_AT_CURRENT_REGION_NAME,
    LEFT_TO_RIGHT,
    REGION_DIRECTION,
    RIGHT_TO_LEFT,
    UP_TO_DOWN,
)


@dataclass
class TrackLineCount:
    """One tracked object's crossing state. Mirrors VisRax's TagCountLineCross."""

    track_id: int
    class_name: str = "unknown"
    global_id: Optional[str] = None
    first_seen_in_region: str = GLOBAL
    last_seen_in_region: str = GLOBAL
    region_name: str = GLOBAL
    is_line_crossed: bool = False
    first_seen_ts: float = field(default_factory=time.time)
    last_seen_ts: float = field(default_factory=time.time)
    in_count: int = 0
    out_count: int = 0
    left_count: int = 0
    right_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "class_name": self.class_name,
            "global_id": self.global_id,
            "first_seen_in_region": self.first_seen_in_region,
            "last_seen_in_region": self.last_seen_in_region,
            "region_name": self.region_name,
            "is_line_crossed": self.is_line_crossed,
            "first_seen_ts": self.first_seen_ts,
            "last_seen_ts": self.last_seen_ts,
            "in_count": self.in_count,
            "out_count": self.out_count,
            "left_count": self.left_count,
            "right_count": self.right_count,
        }


class LinePassingKPI:
    """
    Per-camera line crossing counter.

    State lives on the instance (one per camera) rather than being threaded
    through each call as VisRax's PRESERVED_DATA does — the OmniTrack pipeline
    keeps a long-lived object per camera, so the round-trip through a
    serialized result is unnecessary here.
    """

    def __init__(self) -> None:
        self.tracks: Dict[int, TrackLineCount] = {}

    # ── main entry ────────────────────────────────────────────────
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
            crossed = bool(detection.get(IS_REGION_CROSSED, False))
            direction = detection.get(REGION_DIRECTION)

            track = self.tracks.get(track_id)
            if track is None:
                track = self._create_track(detection, track_id, current_region, crossed, now)
                self.tracks[track_id] = track
                # A track whose very first sighting is already past the line is
                # handled by the global-origin guard inside _count.
                self._count(track, "", False, current_region, direction)
            else:
                prev_region = (track.last_seen_in_region or "").strip().lower()
                prev_crossed = bool(track.is_line_crossed)

                track.last_seen_ts = now
                track.is_line_crossed = crossed
                track.last_seen_in_region = current_region
                track.region_name = current_region

                self._count(track, prev_region, prev_crossed, current_region, direction)

    # ── internals ─────────────────────────────────────────────────
    def _create_track(
        self,
        detection: Dict[str, Any],
        track_id: int,
        current_region: str,
        crossed: bool,
        now: float,
    ) -> TrackLineCount:
        return TrackLineCount(
            track_id=track_id,
            class_name=str(detection.get("class_name", "unknown")),
            global_id=detection.get("global_id"),
            first_seen_in_region=current_region,
            last_seen_in_region=current_region,
            region_name=current_region,
            is_line_crossed=crossed,
            first_seen_ts=now,
            last_seen_ts=now,
        )

    def _count(
        self,
        track: TrackLineCount,
        prev_region: str,
        prev_crossed: bool,
        current_region: str,
        direction: Optional[str],
    ) -> None:
        """Map one crossing event onto exactly one counter."""
        if direction is None:
            # Without a direction there is no way to know which counter to move.
            return
        if not track.is_line_crossed:
            return

        # Already counted for this same region — but a DIFFERENT line may still
        # be crossed later by the same track, which is allowed.
        if prev_crossed and prev_region == (current_region or "").strip().lower():
            return

        first_region = (track.first_seen_in_region or "").strip().lower()
        last_region = (current_region or "").strip().lower()
        global_region = GLOBAL.strip().lower()

        # Only a global → line transition counts.
        if not first_region or first_region != global_region:
            return
        if not last_region or last_region == global_region:
            return

        if direction == UP_TO_DOWN:
            track.in_count += 1
        elif direction == DOWN_TO_UP:
            track.out_count += 1
        elif direction == LEFT_TO_RIGHT:
            track.right_count += 1
        elif direction == RIGHT_TO_LEFT:
            track.left_count += 1

    # ── reporting ─────────────────────────────────────────────────
    def totals_by_region(self) -> Dict[str, Dict[str, int]]:
        """
        Per-region totals. Aggregating the LATEST state of each track (rather
        than summing every frame's delta) is the same guarantee VisRax gets from
        its `DISTINCT ON (track_id) ... ORDER BY at_us DESC` query.
        """
        totals: Dict[str, Dict[str, int]] = {}
        for track in self.tracks.values():
            region = track.last_seen_in_region or GLOBAL
            if region == GLOBAL:
                continue
            bucket = totals.setdefault(
                region,
                {"in": 0, "out": 0, "left": 0, "right": 0, "tracks": 0},
            )
            bucket["in"] += track.in_count
            bucket["out"] += track.out_count
            bucket["left"] += track.left_count
            bucket["right"] += track.right_count
            bucket["tracks"] += 1
        return totals

    def totals_by_class(self) -> Dict[str, int]:
        """How many crossings each class contributed, across all lines."""
        by_class: Dict[str, int] = {}
        for track in self.tracks.values():
            crossings = track.in_count + track.out_count + track.left_count + track.right_count
            if crossings:
                by_class[track.class_name] = by_class.get(track.class_name, 0) + crossings
        return by_class

    def snapshot(self) -> Dict[str, Any]:
        return {
            "by_region": self.totals_by_region(),
            "by_class": self.totals_by_class(),
            "tracked": len(self.tracks),
        }

    def tracks_as_list(self) -> List[Dict[str, Any]]:
        return [t.as_dict() for t in self.tracks.values()]

    def reset(self) -> None:
        self.tracks.clear()
