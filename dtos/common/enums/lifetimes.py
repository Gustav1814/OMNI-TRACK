# dtos/common/enums/job_status.py
from enum import StrEnum


class ServiceLifetime(StrEnum):
    TRANSIENT = "transient"
    SINGLETON = "singleton"
    SCOPED = "scoped"
