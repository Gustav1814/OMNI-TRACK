from enum import StrEnum


class JobStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    PAUSED = "paused"
    CANCELLED = "cancelled"
