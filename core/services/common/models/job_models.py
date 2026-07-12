from datetime import UTC, datetime
from typing import Any

import numpy
from pydantic import BaseModel, ConfigDict, Field

from config.BaseConfig import config
from dtos.common.enums.job_status import JobStatus
from dtos.request.request_register_use_case import ModelInfo, Region
from services.common.models.yolo_models import KPIResult


class JobConfig(BaseModel):
    job_id: str
    camera_id: str
    location_id: str
    kpi_name: str
    url: str
    url_type: str
    tag: str
    height: int | None = None
    width: int | None = None
    snapshots_at_tag: str | None
    tag_at_reid: str | None
    is_global_reid: bool = False
    regions: list[Region] | None
    model_id: str | None = None  # Model ID to use from ModelRegistry
    model_infos: list[ModelInfo] | None = []
    track_name: str = Field(default_factory=lambda: config.FALLBACK_TRACKER)
    tracker_config: dict[str, Any] | None = None
    tracker_model_used: str | None = None
    created_at: str = datetime.now(UTC).isoformat()
    job_status: str = JobStatus.PAUSED.value
    started_at: str | None
    frames_processed: int | None
    current_kpi_value: dict | None
    current_frame: str | None
    numpy_frame: numpy.ndarray | None = Field(default=None, exclude=True)
    raw_frame: numpy.ndarray | None = Field(default=None, exclude=True)
    last_detections: list[dict[str, Any]] | None = Field(default=None, exclude=True)
    frame_number: int = 0
    last_frame_result: KPIResult | None
    summary: KPIResult | dict[str, dict[str, Any]] | None = None
    num_of_frame_per_sec: int
    redis_job_ttl_seconds: int = Field(
        default=86400,
        ge=1,
        description="TTL for the Redis job config key (seconds). Default 1 day.",
    )
    redis_detection_log_ttl_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "TTL for detection/KPI log list keys (seconds); refreshed on each log write. "
            "Default 5 minutes."
        ),
    )
    last_processing_time: str | None
    error_message: str | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)
