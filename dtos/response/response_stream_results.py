from typing import Any

from pydantic import BaseModel

from dtos.common.enums.job_status import JobStatus


class ResponseStreamWithFrames(BaseModel):
    job_status: str = JobStatus.PAUSED.value
    started_at: str | None
    frames_processed: int | None
    current_kpi_value: dict | None
    current_frame: str | None
    last_kpi_results: list[dict[str, Any]] | None
    last_processing_time: str | None
