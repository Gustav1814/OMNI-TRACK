# common/dtos/response/response_check_health.py

from pydantic import BaseModel


class ResponseCheckHealth(BaseModel):
    redis_connected: bool
    total_running_jobs: int
    api_status: str
    uptime_seconds: float
    memory_usage_mb: float
