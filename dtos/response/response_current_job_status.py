# common/dtos/response/response_current_job_status.py

# dtos/response/response_current_job_status.py
from pydantic import BaseModel, ConfigDict

from dtos.common.enums.job_status import JobStatus


class ResponseCurrentJobStatus(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: str
    job_status: JobStatus
    camera_id: str | None = None
    location_id: str | None = None
    kpi_name: str | None = None
    model_name: str | None = None
    device: str | None = None  # gpu/cpu
    frames_processed: int | None = 0
    current_kpi_value: int | None = 0
    last_updated: str | None = None
    error_message: str | None = None
