from datetime import datetime
from typing import Any

from numpy import ndarray
from pydantic import BaseModel, ConfigDict, Field, SerializationInfo, field_serializer


class ODLogging(BaseModel):
    first_seen_in_region: str = ""
    region_name: str = ""
    class_name: str
    first_seen_timestamp: datetime | None = None
    guid_id: str
    bbox: list[int] = Field(default_factory=list)  # [x_min, y_min, x_max, y_max]

    @field_serializer("first_seen_timestamp")
    def serialize_first_seen_timestamp(
        self, dt: datetime | None, _info: SerializationInfo
    ) -> str | None:
        if dt is not None:
            return dt.isoformat()
        return None


class OBCount(ODLogging):
    # Default to empty string so region fields are optional for KPIs that don't use them
    last_seen_in_region: str = ""
    last_seen_timestamp: datetime | None = None

    @field_serializer("last_seen_timestamp")
    def serialize_last_seen_timestamp(
        self, dt: datetime | None, _info: SerializationInfo
    ) -> str | None:
        if dt is not None:
            return dt.isoformat()
        return None


class ROICount(OBCount):
    visited_regions: str = ""  # roiA,roiB,roiC comma separated string
    track_id: int


class ROICountTime(ROICount):
    """
    Extension of ROICount that also keeps track of how long a track
    spent in each visited region.

    - visited_regions: comma separated list of region names in the
      order they were visited.
    - region_with_time: comma separated sequence of region and time
      in microseconds:

          "regionA,1000000,regionB,500000"

      where each pair is (region_name, time_in_microseconds).
    - time_in_visited_regions: total time (in microseconds) across all
      visited regions, stored as a string for consistency.
    - current_region_dwell_time_us: dwell time in current region in microseconds (for calculations)
    """

    region_with_time: str = ""  # "regionA,1000000,regionB,500000"

    current_region_dwell_time_us: int = 0  # Dwell time in current region (microseconds)


class TagCount(ROICountTime):
    is_line_crossed: bool = False
    track_id: int
    current_unique_objects_in_region: int = 0
    # For group-vs-individual KPI we use string group ids (e.g. "5-9-12").
    # Other KPIs can still store integers; Pydantic will coerce them to str.
    group_id: str | None = None
    is_grouped: bool = False

    @field_serializer("first_seen_timestamp")
    def serialize_first_seen_timestamp(
        self, dt: datetime | None, _info: SerializationInfo
    ) -> str | None:
        if dt is not None:
            return dt.isoformat()
        return None


class TagCountLineCross(TagCount):
    in_count: int = 0
    out_count: int = 0
    left_count: int = 0
    right_count: int = 0


class TagCountTime(TagCount):
    started_date: datetime | None = Field(default=None, exclude=True)
    total_time_in_sec: int | None = None
    in_region_count: int = 0
    total_count: int = 0


class KPIResult(BaseModel):
    kpi_name: str
    object_name: str  # Cross-frame pose-attendance track state (must be a Field so ``model_dump(mode="json")`` survives Redis/proxy).
    attendance_track_states: dict[int, dict[str, Any]] | None = Field(default=None)
    tag_counts: dict[str, TagCount] = Field(default_factory=dict)
    general_ob: list[ODLogging] = Field(default_factory=list)
    roi_count: dict[int, ROICount] = Field(default_factory=dict)
    reg_tag_counts: dict[str, dict[str, TagCount]] = Field(default_factory=dict)
    reg_tag_counts_time: dict[str, dict[int, TagCountTime]] = Field(default_factory=dict)
    line_tag_counts: dict[int, TagCount] = Field(default_factory=dict)
    roi_dependent: dict[int, ROICountTime] = Field(default_factory=dict)


class YoloCountItem(BaseModel):
    detection: list[dict[str, Any]] = []
    kpi_results: KPIResult | None = None
    total_detections: Any = []
    enhanced_detections: Any = []
    regions: Any = []
    timestamp: str | None = None
    processing_success: bool
    regions_count: Any = None
    frame_base64: str | None = None
    requested_frame_base64: str | None = None
    annotated_frame: ndarray | None = None
    requested_annotated_frame: ndarray | None = None
    error_message: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
