# services/kpis/line_passing/base_line_kpi.py
import asyncio
import datetime
import os
from abc import ABC
from typing import Any

import numpy as np

from constants.detections_constant import (
    ALL,
    BBOX,
    CLASS_NAME,
    GUID_ID,
    IS_INTEREST_REGION_CROSSED,
    ITEM_AT_CURRENT_REGION_NAME,
    KWARG_SNAPSHOT_TAGS,
    KWARG_TAG,
    TRACK_ID,
    Error_Catcher,
)
from constants.kpi_names_constant import (
    KPI_NAME_ROI_REGION_DEPENDENCY_OBJECT_DETECTION,
    LINE_TAG_COUNTS,
)
from constants.region_names import PRESERVED_DATA
from services.common.models.yolo_models import KPIResult, ROICountTime
from services.kpis.ikpi import IKPI, BaseKPI
from services.nats_streamer_service import NatsStreamerService


class ROIRegionDependencyObjectDetection(BaseKPI, IKPI, ABC):
    """
    Abstract base class for all line-crossing / region-based counting KPIs.
    Handles common logic: tracking persistence, snapshot capture, filtering.
    Subclasses define *what constitutes a crossing event*.
    """

    def __init__(self):
        super().__init__()
        self.kpi_name = KPI_NAME_ROI_REGION_DEPENDENCY_OBJECT_DETECTION
        self.object_name = LINE_TAG_COUNTS
        self.subject = os.getenv("AIS_NATS_SUBJECT", "DetectionStream")

    # ------------------------------------------------------------------
    # Deduplication helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Core compute
    # ------------------------------------------------------------------

    async def compute(
        self, frame: np.ndarray, detections: list[dict[str, Any]], **kwargs: Any
    ) -> KPIResult:
        tag: str = kwargs.get(KWARG_TAG, ALL)
        snapshot_tags: list[str] = kwargs.get(KWARG_SNAPSHOT_TAGS, "")
        preserved_summary: dict[int, ROICountTime] | None = self._get_preserved_summary(kwargs)
        current_time = kwargs.get("current_time", datetime.datetime.now(datetime.UTC))
        summary: dict[int, ROICountTime] = preserved_summary.copy() if preserved_summary else {}

        for detection in detections:
            if not self._should_process_detection(detection, tag):
                continue

            track_id = detection.get(TRACK_ID)
            if track_id is None:
                continue

            current_region = detection.get(ITEM_AT_CURRENT_REGION_NAME, "")
            crossed = detection.get(IS_INTEREST_REGION_CROSSED, False)

            track_obj = summary.get(track_id)
            if track_obj:
                self._update_existing_track(track_obj, current_time, crossed, current_region)
            else:
                track_obj = self._create_new_track(detection, current_time, current_region, crossed)
                summary[track_id] = track_obj

            # Handle snapshot (delegated to BaseKPI)
            self.save_snapshot_to_disk(
                frame, detection, snapshot_tags, **kwargs.get("all_key_args", {})
            )

            # Build NATS payload and publish only if it is not a duplicate
            frame_height, frame_width = frame.shape[:2] if frame is not None else (None, None)
            payload_bytes = self.make_payload_for_nats(
                detection,
                frame_width=frame_width,
                frame_height=frame_height,
                **kwargs.get("all_key_args", {}),
            )

            if self._should_publish(payload_bytes):
                asyncio.create_task(NatsStreamerService.publish(self.subject, payload_bytes))

            # Let subclass decide if this detection triggers a count event
            self._process_potential_crossing(track_obj, detection, frame)

        return KPIResult(
            roi_dependent=summary,
            kpi_name=self.kpi_name,
            object_name=self.object_name,
        )

    def _get_preserved_summary(self, kwargs: Any) -> dict[int, ROICountTime]:
        preserved_result: KPIResult | None = kwargs.get(PRESERVED_DATA)
        return preserved_result.roi_dependent if preserved_result else {}

    def _should_process_detection(self, detection: dict[str, Any], tag: str) -> bool:
        class_name = detection.get(CLASS_NAME, "")
        return tag == ALL or class_name == tag

    def _update_existing_track(
        self,
        track_obj: ROICountTime,
        current_time: datetime.datetime,
        _crossed: bool,
        current_region: str,
    ) -> None:
        """
        Update an existing track, computing time spent in the previous
        region and updating the per‑region breakdown.
        """
        delta_us = self._elapsed_us(track_obj.last_seen_timestamp, current_time)
        regions_order, durations = self._parse_region_history(track_obj)

        if not regions_order and current_region:
            regions_order, durations = [current_region], [0]

        if durations:
            durations[-1] += delta_us

        if current_region and (not regions_order or current_region != regions_order[-1]):
            regions_order.append(current_region)
            durations.append(0)

        regions_order, durations = self._merge_consecutive_regions(regions_order, durations)

        track_obj.visited_regions = ",".join(regions_order)
        track_obj.region_with_time = ",".join(str(t) for t in durations)
        track_obj.current_region_dwell_time_us = durations[-1] if durations else 0
        track_obj.last_seen_timestamp = current_time
        track_obj.last_seen_in_region = current_region
        track_obj.region_name = current_region

    @staticmethod
    def _elapsed_us(last_seen: datetime.datetime | None, current_time: datetime.datetime) -> int:
        if last_seen is None:
            return 0
        return int((current_time - last_seen).total_seconds() * 1_000_000)

    @staticmethod
    def _parse_region_history(track_obj: ROICountTime) -> tuple[list[str], list[int]]:
        regions: list[str] = [r for r in (track_obj.visited_regions or "").split(",") if r]
        durations: list[int] = []
        for token in (track_obj.region_with_time or "").split(","):
            if not token:
                continue
            try:
                durations.append(int(token))
            except ValueError:
                durations.append(0)

        # Align durations length to regions count
        if len(durations) < len(regions):
            durations.extend([0] * (len(regions) - len(durations)))
        else:
            durations = durations[: len(regions)]

        return regions, durations

    @staticmethod
    def _merge_consecutive_regions(
        regions: list[str], durations: list[int]
    ) -> tuple[list[str], list[int]]:
        merged_regions: list[str] = []
        merged_durations: list[int] = []
        for reg, dur in zip(regions, durations, strict=True):
            if merged_regions and reg == merged_regions[-1]:
                merged_durations[-1] += dur
            else:
                merged_regions.append(reg)
                merged_durations.append(dur)
        return merged_regions, merged_durations

    def _create_new_track(
        self,
        detection: dict[str, Any],
        current_time: datetime.datetime,
        current_region: str,
        _crossed: bool,
    ) -> ROICountTime:
        class_name = detection.get(CLASS_NAME, "")
        track_id = detection[TRACK_ID]
        guid_id = detection.get(GUID_ID, Error_Catcher)
        bbox = detection.get(BBOX, [])  # [x_min, y_min, x_max, y_max]

        return ROICountTime(
            track_id=track_id,
            class_name=class_name,
            first_seen_timestamp=current_time,
            last_seen_timestamp=current_time,
            first_seen_in_region=current_region,
            last_seen_in_region=current_region,
            region_name=current_region,
            visited_regions=current_region if current_region else "",
            region_with_time="0" if current_region else "",
            current_region_dwell_time_us=0,
            guid_id=guid_id,
            bbox=bbox,
        )

    def enhance_detections_with_time(
        self,
        detections: list[dict[str, Any]],
        kpi_results: KPIResult,
    ) -> list[dict[str, Any]]:
        """
        Enhance detections with time information from KPI results.
        All time calculations are already done in the KPI.
        """
        if not kpi_results or not hasattr(kpi_results, "line_tag_counts"):
            return detections

        enhanced = []

        for detection in detections:
            track_id = detection.get(TRACK_ID)
            if track_id is None:
                enhanced.append(detection)
                continue

            enhanced.append(detection)

        return enhanced

    # === Hook for Subclasses ===

    def _process_potential_crossing(
        self, track_obj: ROICountTime, detection: dict[str, Any], frame: np.ndarray
    ) -> None:
        """
        Subclasses implement crossing logic here (e.g., detect direction, count once, etc.).
        This is called *every frame* for tracked objects.
        """
        pass
