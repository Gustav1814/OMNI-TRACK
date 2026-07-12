# services/interfaces/ibusiness_logic_service.py
import asyncio
import base64
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any

import cv2
import psutil

from core.container import container
from dtos.request.request_register_use_case import RequestRegisterUseCase
from host.datasource.redis_client import redis_client
from services.common.models.job_models import JobConfig
from services.core.interfaces.ibackground_job_manager import IBackgroundJobManager
from services.core.interfaces.ilogger_service import ILoggerService
from services.db_sources.redis_repos.iredis_service import IRedisService
from services.interfaces.imodel_service import IModelService


class IBusinessLogicService(ABC):
    start_time: float = time.time()

    @abstractmethod
    async def register_job(self, request: RequestRegisterUseCase) -> str:
        pass

    @abstractmethod
    async def unregister_job(self, job_id: str) -> bool:
        # Stop background job
        job_manager = container.resolve(IBackgroundJobManager)
        await job_manager.stop_job(job_id)

        # Remove from Redis
        redis_service = container.resolve(IRedisService)
        await redis_service.delete_job(job_id)

        logger = container.resolve(ILoggerService)
        await logger.info(f"Unregistered job {job_id}")

        return True

    async def verify_inference(self, request: RequestRegisterUseCase) -> dict[str, Any]:
        model_service = container.resolve(IModelService)
        logger = container.resolve(ILoggerService)

        try:
            # Capture single frame
            req_url = request.url
            if request.url_type == "youtube":
                req_url = self.get_youtube_stream_url(request.url)

            cap = cv2.VideoCapture(req_url)
            ret, frame = cap.read()
            cap.release()

            if not ret:
                raise Exception("Could not capture frame from URL")

            # Run inference
            detections = await model_service.predict(frame)
            regions = request.regions or []
            enhanced_detections = await model_service.check_detections_in_regions(
                detections, [region.dict() for region in regions]
            )

            # Draw regions and detections on frame
            annotated_frame = self._draw_annotations(frame, enhanced_detections, regions)

            # Convert frame to base64
            _, buffer = cv2.imencode(".jpg", annotated_frame)
            frame_base64 = base64.b64encode(buffer.tobytes()).decode("utf-8")

            # Calculate KPIs
            person_count = sum(
                1 for d in enhanced_detections if d["class_name"] == "person" and d["in_region"]
            )

            result = {
                "frame_base64": frame_base64,
                "total_detections": len(enhanced_detections),
                "person_count": person_count,
                "detections": enhanced_detections,
                "regions_count": len(regions),
            }

            await logger.info(f"Verification completed for camera {request.camera_id}")
            return result

        except Exception as e:
            await logger.error(f"Verification failed: {e}")
            raise

    def _draw_annotations(self, frame, detections, regions):
        import cv2
        import numpy as np

        # Draw regions
        for region in regions or []:
            if region.type == "polygon":
                points = np.array([[p.x, p.y] for p in region.points], np.int32)
                cv2.polylines(frame, [points], True, (0, 255, 0), 2)
                cv2.putText(
                    frame,
                    region.name,
                    (points[0][0], points[0][1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )
            elif region.type == "bounding_box":
                coords = region.coordinates
                cv2.rectangle(
                    frame,
                    (coords.x_min, coords.y_min),
                    (coords.x_max, coords.y_max),
                    (0, 255, 0),
                    2,
                )
                cv2.putText(
                    frame,
                    region.name,
                    (coords.x_min, coords.y_min - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )

        # Draw detections
        for detection in detections:
            bbox = detection["bbox"]
            color = (0, 255, 255) if detection["in_region"] else (255, 0, 0)
            cv2.rectangle(frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), color, 2)

            label = f"{detection['class_name']} {detection['confidence']:.2f}"
            if detection["in_region"]:
                label += f" ({detection['region_name']})"

            cv2.putText(
                frame, label, (bbox[0], bbox[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1
            )

        return frame

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        job_manager: IBackgroundJobManager = container.resolve(IBackgroundJobManager)
        redis_service: IRedisService = container.resolve(IRedisService)
        model_service = container.resolve(IModelService)

        status = await job_manager.get_job_status(job_id)
        job_data = await redis_service.get_job(job_id)
        model_info = model_service.get_model_info()

        if job_data and status:
            job_data.job_status = status.job_status
            job_data.current_kpi_value = status.current_kpi_value
            job_data.frames_processed = status.frames_processed

            return {
                **job_data.model_dump(),
                "error_message": getattr(status, "error_message", None),
                "model_name": model_info.get("model_name"),
                "device": model_info.get("device"),
            }

        return {"job_id": job_id, "job_status": "not_found"}

    def get_youtube_stream_url(self, youtube_url: str) -> str:
        """
        Convert a YouTube URL to its direct stream URL using youtube-dl.
        """
        try:
            # Use youtube-dl to get the direct stream URL
            result = subprocess.run(
                ["youtube-dl", "-f", "best", "-g", youtube_url],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                stream_url = result.stdout.strip()
                return stream_url
            else:
                raise Exception(f"youtube-dl error: {result.stderr.strip()}")
        except Exception as e:
            logger = container.resolve(ILoggerService)
            asyncio.run(logger.error(f"Failed to get YouTube stream URL: {str(e)}"))
            return ""

    async def get_health_status(self) -> dict[str, Any]:
        try:
            # Check Redis connection
            redis_connected = await redis_client.exists("test")
            redis_connected = True
        except Exception as e:
            print(e)
            redis_connected = False

        # Get running jobs count
        job_manager = container.resolve(IBackgroundJobManager)
        running_jobs = await job_manager.get_all_running_jobs() or []

        # Get system info
        memory_usage = psutil.virtual_memory().used / 1024 / 1024  # MB
        uptime = time.time() - getattr(self, "start_time", time.time())

        return {
            "redis_connected": redis_connected,
            "total_running_jobs": len(running_jobs),
            "api_status": "healthy" if redis_connected else "degraded",
            "uptime_seconds": uptime,
            "memory_usage_mb": memory_usage,
        }

    @abstractmethod
    async def get_all_jobs(self, status: str) -> dict[str, Any]:
        """Get all jobs with optional status filtering."""
        pass

    @abstractmethod
    async def get_running_jobs(self) -> list[JobConfig]:
        """
        Jobs that are running. Merges Redis (cross-worker) with local process state;
        when both exist, local manager state wins so ERROR/CANCELLED is not masked.
        """
        pass

    @abstractmethod
    async def get_job_details(self, job_id: str) -> dict[str, Any]:
        """Get complete job configuration details with stages, models, and regions."""
        pass

    @abstractmethod
    async def get_activity_types(self) -> dict[str, Any]:
        """Get all available activity types (KPIs) for job creation."""
        pass

    @abstractmethod
    async def get_stages_by_activity(self, activity_type_id: str) -> dict[str, Any]:
        """Get stages and models for a specific activity type."""
        pass

    @abstractmethod
    async def get_model_classes(self, model_id: str) -> dict[str, Any]:
        """Get class names for a specific model."""
        pass

    @abstractmethod
    async def patch_running_job_config(self, job_id: str, update_data: dict[str, Any]) -> JobConfig:
        """
        Partially update job configuration (PATCH).
        Similar to update but with better error handling for missing jobs.
        """
        pass
