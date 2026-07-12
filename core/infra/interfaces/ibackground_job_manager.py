# services/interfaces/ibackground_job_manager.py
import asyncio
from abc import ABC, abstractmethod

from services.common.models.job_models import JobConfig


class IBackgroundJobManager(ABC):
    running_jobs: dict[str, asyncio.Task] = {}

    job_statuses: dict[str, JobConfig] = {}

    @abstractmethod
    async def start_job(self, job_id: str, job_config: JobConfig) -> bool:
        pass

    @abstractmethod
    async def stop_job(self, job_id: str) -> bool:
        pass

    @abstractmethod
    async def get_job_status(self, job_id: str) -> JobConfig | None:
        pass

    @abstractmethod
    async def get_all_running_jobs(self) -> list[JobConfig] | None:
        pass

    @abstractmethod
    async def resume_jobs_from_redis(self) -> bool:
        pass

    @abstractmethod
    async def get_background_job(self, job_id: str) -> JobConfig | None:
        pass

    @abstractmethod
    async def update_job_property(self, job_id: str, property_name: str, value: int) -> bool:
        pass
