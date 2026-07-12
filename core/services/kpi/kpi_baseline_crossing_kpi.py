# services/kpis/line_passing/base_line_kpi.py
import datetime
from abc import ABC
from typing import Any, cast

import numpy as np

from constants.camera_direction import DOWN_TO_UP, LEFT_TO_RIGHT, RIGHT_TO_LEFT, UP_TO_DOWN
from constants.detections_constant import (
    ALL,
    BBOX,
    CLASS_NAME,
    GLOBAL,
    GUID_ID,
    IS_INTEREST_REGION_CROSSED,
    ITEM_AT_CURRENT_REGION_NAME,
    KWARG_SNAPSHOT_TAGS,
    KWARG_TAG,
    TRACK_ID,
    Error_Catcher,
)
from constants.kpi_names_constant import KPI_NAME_GENERAL_OBJECT_DETECTION, LINE_TAG_COUNTS
from constants.region_names import PRESERVED_DATA
from services.common.models.yolo_models import KPIResult, TagCount, TagCountLineCross
from services.kpis.ikpi import IKPI, BaseKPI


class BaseLineCrossingKPI(BaseKPI, IKPI, ABC):
    """
    Abstract base class for all line-crossing / region-based counting KPIs.
    Handles common logic: tracking persistence, snapshot capture, filtering.
    Subclasses define *what constitutes a crossing event*.
    """

    def __init__(self):
        super().__init__()
        self.kpi_name = KPI_NAME_GENERAL_OBJECT_DETECTION
        self.object_name = LINE_TAG_COUNTS

    async def compute(
        self,
        frame: np.ndarray,
        detections: list[dict[str, Any]],
        **kwargs: Any,  # camera direction through kwargs up,right,down,left
    ) -> KPIResult:
        tag: str = kwargs.get(KWARG_TAG, ALL)
        snapshot_tags: list[str] = kwargs.get(KWARG_SNAPSHOT_TAGS, "")
        key_args: dict[str, Any] = kwargs.get("all_key_args") or {}

        # Create a mapping from region name to camera direction
        # This supports any number of line regions (1, 2, 3, or 4 lines) where each line
        # can have its own camera direction (UP_TO_DOWN, DOWN_TO_UP, LEFT_TO_RIGHT, RIGHT_TO_LEFT)
        # Example: {"line_in": UP_TO_DOWN, "line_out": DOWN_TO_UP, "line_left": RIGHT_TO_LEFT, "line_right": LEFT_TO_RIGHT}
        region_to_direction: dict[str, str | None] = {}
        for region_name, direction in key_args.items():
            region_to_direction[region_name] = direction

        # Fallback: if no region-specific directions, use first available (backward compatibility)
        # This handles cases where detections are in GLOBAL region or regions not in the mapping
        default_camera_direction: str | None = None
        if region_to_direction:
            default_camera_direction = next(iter(region_to_direction.values()), None)

        preserved_summary: dict[int, TagCountLineCross] = self._get_preserved_summary(kwargs)

        current_time = kwargs.get("current_time", datetime.datetime.now(datetime.UTC))
        summary: dict[int, TagCountLineCross] = (
            preserved_summary.copy() if preserved_summary else {}
        )

        for detection in detections:
            if not self._should_process_detection(detection, tag):
                continue

            track_id = detection.get(TRACK_ID)
            if track_id is None:
                continue

            current_region = detection.get(ITEM_AT_CURRENT_REGION_NAME, "")
            crossed = detection.get(IS_INTEREST_REGION_CROSSED, False)

            # Get camera direction for this specific region
            # If the detection is in a specific region, use that region's direction
            # Otherwise, fall back to default (for backward compatibility)
            camera_direction = region_to_direction.get(current_region, default_camera_direction)

            track_obj = summary.get(track_id)
            if track_obj:
                self._update_existing_track(
                    track_obj,
                    current_time,
                    crossed,
                    current_region,
                    camera_direction,
                )
            else:
                track_obj = self._create_new_track(
                    detection,
                    current_time,
                    current_region,
                    crossed,
                    camera_direction,
                )
                summary[track_id] = track_obj

            # Handle snapshot (delegated to BaseKPI)
            self.save_snapshot_to_disk(
                frame, detection, snapshot_tags, **kwargs.get("all_key_args", {})
            )
            # self.append_snapshots(frame, detection, track_obj, snapshot_tags)
            # self.add_pgvector_entry(frame, detection, tag_at_reid,**kwargs.get("all_key_args",{}))

            # Let subclass decide if this detection triggers a count event
            self._process_potential_crossing(track_obj, detection, frame)

        return KPIResult(
            line_tag_counts=cast(dict[int, TagCount], summary),
            kpi_name=self.kpi_name,
            object_name=self.object_name,
        )

    def _get_preserved_summary(self, kwargs: Any) -> dict[int, TagCountLineCross]:
        preserved_result: KPIResult | None = kwargs.get(PRESERVED_DATA)
        if preserved_result and preserved_result.line_tag_counts:
            # Existing entries may be TagCount or TagCountLineCross; coerce to TagCountLineCross
            coerced: dict[int, TagCountLineCross] = {}
            for track_id, tag in preserved_result.line_tag_counts.items():
                if isinstance(tag, TagCountLineCross):
                    coerced[track_id] = tag
                else:
                    # Create a fresh TagCountLineCross preserving common fields
                    coerced[track_id] = TagCountLineCross(
                        track_id=tag.track_id,
                        class_name=tag.class_name,
                        first_seen_timestamp=tag.first_seen_timestamp,
                        last_seen_timestamp=tag.last_seen_timestamp,
                        first_seen_in_region=tag.first_seen_in_region,
                        last_seen_in_region=tag.last_seen_in_region,
                        region_name=tag.region_name or tag.last_seen_in_region,
                        is_line_crossed=tag.is_line_crossed,
                        guid_id=tag.guid_id,
                        bbox=tag.bbox,
                    )
            return coerced
        return {}

    def _should_process_detection(self, detection: dict[str, Any], tag: str) -> bool:
        class_name = detection.get(CLASS_NAME, "")
        return tag == ALL or class_name == tag

    def _update_existing_track(
        self,
        track_obj: TagCountLineCross,
        current_time: datetime.datetime,
        crossed: bool,
        current_region: str,
        camera_direction: str | None,
    ) -> None:
        prev_last_region = (track_obj.last_seen_in_region or "").strip().lower()
        prev_is_crossed = bool(track_obj.is_line_crossed)

        track_obj.last_seen_timestamp = current_time
        track_obj.is_line_crossed = crossed
        track_obj.last_seen_in_region = current_region
        track_obj.region_name = current_region

        # Update directional counts once, when an object crosses the line for the first time
        self._update_directional_counts(
            track_obj=track_obj,
            prev_last_region=prev_last_region,
            prev_is_crossed=prev_is_crossed,
            current_region=current_region,
            camera_direction=camera_direction,
        )

    def _create_new_track(
        self,
        detection: dict[str, Any],
        current_time: datetime.datetime,
        current_region: str,
        crossed: bool,
        camera_direction: str | None,
    ) -> TagCountLineCross:
        class_name = detection.get(CLASS_NAME, "")
        track_id = detection[TRACK_ID]
        guid_id = detection.get(GUID_ID, Error_Catcher)
        bbox = detection.get(BBOX, [])  # [x_min, y_min, x_max, y_max]
        track_obj = TagCountLineCross(
            track_id=track_id,
            class_name=class_name,
            first_seen_timestamp=current_time,
            last_seen_timestamp=current_time,
            first_seen_in_region=current_region,
            last_seen_in_region=current_region,
            region_name=current_region,
            is_line_crossed=crossed,
            guid_id=guid_id,
            bbox=bbox,
        )

        # If the very first detection for this track is already marked as crossed,
        # update directional counts immediately.
        self._update_directional_counts(
            track_obj=track_obj,
            prev_last_region="",
            prev_is_crossed=False,
            current_region=current_region,
            camera_direction=camera_direction,
        )
        return track_obj

    def _update_directional_counts(
        self,
        track_obj: TagCountLineCross,
        prev_last_region: str,
        prev_is_crossed: bool,
        current_region: str,
        camera_direction: str | None,
    ) -> None:
        """
        Map a single crossing event to one of: in_count, out_count, left_count, right_count.
        We treat a crossing as: first_seen_region == GLOBAL and last_seen_region == line_name.
        """
        if camera_direction is None:
            # Without camera direction we cannot know whether this is in/out/left/right.
            return

        # Only count when the object has actually crossed a line *in this frame*
        # (is_line_crossed == True), and avoid double‑counting the same line
        # for the same track across consecutive frames.
        if not track_obj.is_line_crossed:
            return

        # If the track was already in a "crossed" state *and* the last region is
        # the same as the current one, we have already counted this crossing.
        # However, if the object moves on and crosses a *different* line region,
        # we want to allow an additional count for that new region.
        if prev_is_crossed and prev_last_region == current_region:
            return

        first_region = (track_obj.first_seen_in_region or "").strip().lower()
        last_region = (current_region or "").strip().lower()
        global_region = GLOBAL.strip().lower()

        # We only care about transitions from GLOBAL -> line region
        if not first_region or first_region != global_region:
            return
        if not last_region or last_region == global_region:
            return

        # Map camera direction -> which counter to increment
        if camera_direction == UP_TO_DOWN:
            track_obj.in_count += 1
        elif camera_direction == DOWN_TO_UP:
            track_obj.out_count += 1
        elif camera_direction == LEFT_TO_RIGHT:
            track_obj.right_count += 1
        elif camera_direction == RIGHT_TO_LEFT:
            track_obj.left_count += 1

    # === Hook for Subclasses ===

    def _process_potential_crossing(
        self, track_obj: TagCountLineCross, detection: dict[str, Any], frame: np.ndarray
    ) -> None:
        """
        Subclasses implement crossing logic here (e.g., detect direction, count once, etc.).
        This is called *every frame* for tracked objects.
        """
        pass
