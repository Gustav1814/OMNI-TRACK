# common/dtos/response/response_current_running_jobs.py

from pydantic import BaseModel

from dtos.common.enums.job_status import JobStatus
from services.common.models.job_models import JobConfig


class JobInfo(BaseModel):
    job_id: str
    camera_id: str
    location_id: str
    kpi_name: str
    status: JobStatus
    started_at: str
    url_type: str


class ResponseCurrentRunningJobs(BaseModel):
    total_jobs: int
    jobs: list[JobConfig]
