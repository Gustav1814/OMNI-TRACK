# services/business_logic_service.py (Updated)
import time
import uuid
from typing import Any, cast

import httpx

# from idlelib.rpc import request_queue
import psutil
import yaml

from constants.detections_constant import KEYPOINTS, MASK
from core.container import container
from dtos.common.enums.job_status import JobStatus
from dtos.common.enums.url_type import URLType
from dtos.request.request_register_use_case import RequestRegisterUseCase
from host.datasource.redis_client import redis_client
from services.common.models.job_models import JobConfig, Region
from services.common.models.yolo_models import KPIResult
from services.core.interfaces.ibackground_job_manager import IBackgroundJobManager
from services.core.interfaces.ibusiness_logic_service import IBusinessLogicService
from services.core.interfaces.ilogger_service import ILoggerService
from services.db_sources.redis_repos.iredis_service import IRedisService
from services.inferenced_services.interfaces.iframe_processing_service import (
    IFrameProcessingService,
)
from services.interfaces.icache_shared_manager_service import ICacheSharedManagerService
from services.interfaces.imodel_service import IModelService
from utils.url_parsers import get_youtube_stream_url, is_rtsp_url, is_youtube_url


def _validate_regions_for_register_and_verify(
    kpi_name: str,
    regions: list[Region],
    frame_processor: IFrameProcessingService,
) -> None:
    """Shared geometry + KPI region-type validation for register and verify."""
    validation_result = frame_processor.validate_regions(regions)
    if not validation_result["valid"]:
        errors = validation_result.get("errors", []) or []
        raise ValueError(f"Invalid regions: {', '.join(errors)}")
    if regions:
        from services.model.model_registry import ModelRegistry

        region_types = [
            region.type.value if hasattr(region.type, "value") else str(region.type)
            for region in regions
        ]
        is_valid, error_msg = ModelRegistry.validate_regions_for_kpi(kpi_name, region_types)
        if not is_valid:
            raise ValueError(f"Region validation failed: {error_msg}")


class BusinessLogicService(IBusinessLogicService):
    def __init__(self):
        self.start_time = time.time()
        self.frame_processor: IFrameProcessingService = container.resolve(IFrameProcessingService)

    def _get_default_tracker_config(self, tracker_name: str) -> dict[str, Any]:
        from config.BaseConfig import config

        # Try loading from baseconfig TRACKER_DEFAULTS
        if tracker_name in config.TRACKER_DEFAULTS:
            return config.TRACKER_DEFAULTS[tracker_name]

        rtdetr_name = f"rtdetr_{tracker_name}"
        if rtdetr_name in config.TRACKER_DEFAULTS:
            return config.TRACKER_DEFAULTS[rtdetr_name]

        import os

        default_paths = [
            f"services/model/cfgs/trackers/rtdetr_{tracker_name}.yaml",
            f"services/model/cfgs/trackers/{tracker_name}.yaml",
        ]
        for path in default_paths:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        content = yaml.safe_load(f)
                        if isinstance(content, dict):
                            return content
                except Exception:
                    pass
        return {}

    async def _handle_dynamic_config(
        self, request: RequestRegisterUseCase
    ) -> tuple[str, dict[str, Any]]:
        from config.BaseConfig import config

        yaml_content = None
        if request.config_file_content:
            yaml_content = request.config_file_content
        elif request.config_file_url:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(request.config_file_url, timeout=10.0)
                    response.raise_for_status()
                    yaml_content = response.text
            except Exception as e:
                raise ValueError(f"Failed to fetch config file from URL: {e}") from e

        tracker_type = request.track_name or config.FALLBACK_TRACKER
        custom_config = {}
        has_custom = False
        if yaml_content:
            has_custom = True
            try:
                custom_config = yaml.safe_load(yaml_content)
            except Exception as yaml_err:
                import json

                try:
                    custom_config = json.loads(yaml_content)
                except Exception as json_err:
                    raise ValueError(
                        f"Failed to parse custom configuration as YAML or JSON. YAML error: {yaml_err}. JSON error: {json_err}"
                    ) from json_err

            if not isinstance(custom_config, dict):
                raise ValueError("Custom configuration must be a dictionary/object")
            tracker_type = custom_config.get("tracker_type", tracker_type)

        defaults = self._get_default_tracker_config(tracker_type)
        merged_config = {**defaults, **custom_config}

        if has_custom:
            config.TRACKER_CONFIG = merged_config
        else:
            config.TRACKER_CONFIG = {}

        return tracker_type, merged_config

    def _save_custom_tracker_yaml(
        self, tracker_config: dict[str, Any], job_id: str | None = None
    ) -> None:
        import os

        trackers_dir = "services/model/cfgs/trackers"
        os.makedirs(trackers_dir, exist_ok=True)
        job_suffix = f"_{job_id}" if job_id else ""
        custom_yaml_path = os.path.join(trackers_dir, f"custom_tracker{job_suffix}.yaml")
        with open(custom_yaml_path, "w") as f:
            yaml.dump(tracker_config, f)

    async def _resolve_job_id(self, requested_job_id: str | None) -> str:
        """Resolve and validate the job_id for a new job.

        - If provided, ensure it is unique across in-memory and Redis jobs.
        - If null, auto-generate a GUID when ``Is_JOB_GUID_DEFAULT_ALLOWED`` is enabled,
          otherwise raise an error.
        """
        from config.BaseConfig import config

        job_id = (requested_job_id or "").strip()
        if not job_id:
            if not config.IS_JOB_GUID_DEFAULT_ALLOWED:
                raise ValueError("job_id is required")
            return str(uuid.uuid4())

        job_manager = container.resolve(IBackgroundJobManager)
        redis_service = container.resolve(IRedisService)
        if job_id in job_manager.job_statuses or await redis_service.get_job(job_id):
            raise ValueError(f"job_id '{job_id}' already exists")
        return job_id

    async def register_job(self, request: RequestRegisterUseCase) -> str:
        job_id = await self._resolve_job_id(request.job_id)

        # Resolve and save dynamic config
        has_custom = bool(request.config_file_content or request.config_file_url)
        tracker_type, merged_config = await self._handle_dynamic_config(request)
        if has_custom:
            self._save_custom_tracker_yaml(merged_config, job_id)
        else:
            import os

            custom_yaml_path = f"services/model/cfgs/trackers/custom_tracker_{job_id}.yaml"
            if os.path.exists(custom_yaml_path):
                try:
                    os.remove(custom_yaml_path)
                except Exception:
                    pass
        request.track_name = tracker_type

        _validate_regions_for_register_and_verify(
            request.kpi_name,
            list(request.regions or []),
            self.frame_processor,
        )
        # Convert request to job config
        url_type = request.url_type.value
        if is_rtsp_url(request.url):
            url_type = URLType.RTSP.value
        elif is_youtube_url(request.url):
            url_type = URLType.YOUTUBE.value
            new_url = get_youtube_stream_url(request.url)
            if not new_url:
                raise ValueError(f"invalid youtube url:{request.url}")
            request.url = new_url

        regions: list[Region] | None = request.regions

        job_config = JobConfig(
            job_id=job_id,
            camera_id=request.camera_id.lower(),
            location_id=request.location_id.lower(),
            kpi_name=request.kpi_name,
            url=request.url,
            url_type=url_type,
            tag=request.tag.lower(),
            regions=regions,
            model_infos=request.model_infos,
            track_name=request.track_name,
            tracker_config=merged_config if has_custom else None,
            tracker_model_used=tracker_type,
            current_frame=None,
            started_at=None,
            current_kpi_value={},
            frames_processed=None,
            last_frame_result=None,
            num_of_frame_per_sec=request.num_of_frame_per_sec,
            redis_job_ttl_seconds=request.redis_job_ttl_seconds,
            redis_detection_log_ttl_seconds=request.redis_detection_log_ttl_seconds,
            snapshots_at_tag=request.snapshots_at_tag.lower(),
            tag_at_reid=(request.tag_at_reid or "").lower(),
            is_global_reid=request.is_global_reid,  # Default: False (global ReID disabled)
            last_processing_time="",
            summary=KPIResult(kpi_name=request.kpi_name, object_name="", tag_counts={}),
        )

        # Save to Redis
        redis_service = container.resolve(IRedisService)
        await redis_service.save_job(job_id, job_config)

        # Initialize global cache if is_global_reid is enabled
        if job_config.is_global_reid:
            logger = container.resolve(ILoggerService)

            await logger.info(
                f"Initializing global FAISS cache with tag_at_reid: {job_config.tag_at_reid}"
            )

            try:
                # Call gRPC to get global embeddings from pgvector based on tag_at_reid
                # Returns: List[Dict] with format [{"global_id": "G1", "embedding": [512-dim], "object_name": "person"}, ...]
                global_embeddings_data = await self._fetch_global_embeddings_from_grpc(
                    job_config.tag_at_reid or ""
                )

                # 1️⃣ Connect to Weaviate

                from config.BaseConfig import config
                from services.db_sources.repos.weaviate_collection_manager import (
                    WeaviateCollectionManager,
                )
                from services.db_sources.repos.weaviate_connector import WeaviateConnector
                from services.db_sources.repos.weaviate_embedding_repository import (
                    WeaviateEmbeddingRepository,
                )

                connector = WeaviateConnector(
                    host=config.WEAVIATE.AIS_WEAVIATE_HOST,
                    http_port=int(config.WEAVIATE.AIS_WEAVIATE_HTTP_PORT),
                    grpc_port=int(config.WEAVIATE.AIS_WEAVIATE_GRPC_PORT),
                )
                connector.connect()

                # 2️⃣ Ensure collections exist
                collection_manager = WeaviateCollectionManager(
                    connector=connector,
                    global_name="GlobalEmbeddings",
                    local_name="LocalEmbeddings",
                    # embedding_dim=512,
                )
                collection_manager.create_collections()

                # 3️⃣ Create Weaviate service
                weaviate_service = WeaviateEmbeddingRepository(
                    connector=connector,
                    global_collection_name="GlobalEmbeddings",
                    local_collection_name="LocalEmbeddings",
                )

                # 4️⃣ Create cache manager (NO FAISS)
                cache_manager = container.resolve(ICacheSharedManagerService)
                cache_manager.initialize(weaviate_service, logger)

                # 5️⃣ Initialize global cache
                await cache_manager.initialize_cache(global_embeddings_data)

                await logger.info(
                    f"Successfully initialized global cache with {len(global_embeddings_data)} global embeddings"
                )
            except Exception as e:
                await logger.error(f"Failed to initialize global cache: {str(e)}")
                # Continue with job start even if cache initialization fails (graceful degradation)

        # Start background job (in-memory JobConfig switches to RUNNING)
        job_manager = container.resolve(IBackgroundJobManager)
        started = await job_manager.start_job(job_id, job_config)
        if not started:
            await redis_service.delete_job(job_id)
            raise ValueError("Could not start background job (duplicate job_id on this worker).")
        await redis_service.save_job(job_id, job_config)

        logger = container.resolve(ILoggerService)
        await logger.info(f"Registered new job {job_id} for camera {request.camera_id}")

        return job_id

    async def unregister_job(self, job_id: str) -> bool:
        job_manager = container.resolve(IBackgroundJobManager)
        redis_service = container.resolve(IRedisService)
        logger = container.resolve(ILoggerService)

        try:
            # Stop background job
            await job_manager.stop_job(job_id)

            # Remove from Redis
            await redis_service.delete_job(job_id)

            # Clean up job-specific tracker YAML
            import os

            custom_yaml_path = f"services/model/cfgs/trackers/custom_tracker_{job_id}.yaml"
            if os.path.exists(custom_yaml_path):
                try:
                    os.remove(custom_yaml_path)
                except Exception:
                    pass

            await logger.info(f"Unregistered job {job_id}")
            return True
        except Exception as e:
            await logger.error(f"Failed to unregister job {job_id}: {str(e)}")
            return False

    async def patch_running_job_config(self, job_id: str, update_data: dict[str, Any]) -> JobConfig:
        """
        Partially update job configuration with validation and Redis persistence.
        Raises ValueError if job not found.

        Updateable fields: tag, height, width, num_of_frame_per_sec, and other non-immutable fields
        """
        logger = container.resolve(ILoggerService)
        redis_service = container.resolve(IRedisService)
        job_manager = container.resolve(IBackgroundJobManager)

        # Check if job exists first
        if job_id not in job_manager.job_statuses:
            await logger.warning(f"Attempted to patch non-existent job {job_id}")
            raise ValueError(f"Job {job_id} not found or is not running")

        job: JobConfig = job_manager.job_statuses[job_id]

        # Safety net — immutable fields
        IMMUTABLE_FIELDS = {
            "job_id",
            "camera_id",
            "location_id",
            "kpi_name",
            "regions",
            "last_processing_time",
            "summary",
            "last_frame_result",
            "frames_processed",
            "current_frame",
            "started_at",
            "job_status",
            "created_at",
            "model_infos",
            "numpy_frame",
            "url_type",
        }

        # Fields that require validation
        FIELD_VALIDATORS = {
            "num_of_frame_per_sec": lambda x: isinstance(x, int) and x > 0,
            "height": lambda x: isinstance(x, int) and x > 0,
            "width": lambda x: isinstance(x, int) and x > 0,
            "tag": lambda x: isinstance(x, str) and len(x) > 0,
        }

        updated_fields = []
        skipped_fields = []
        validation_errors = []

        for field, value in update_data.items():
            if field in IMMUTABLE_FIELDS:
                skipped_fields.append(field)
                await logger.info(f"Field '{field}' is immutable and cannot be updated")
                continue

            # Validate field if validator exists
            if field in FIELD_VALIDATORS:
                validator = FIELD_VALIDATORS[field]
                if not validator(value):
                    validation_errors.append(f"Invalid value for '{field}': {value}")
                    await logger.warning(f"Validation failed for field '{field}': {value}")
                    continue

            if hasattr(job, field):
                old_value = getattr(job, field)
                setattr(job, field, value)
                updated_fields.append(field)
                await logger.info(f"Updated job {job_id} field '{field}': {old_value} -> {value}")

        # If there were validation errors, raise exception
        if validation_errors:
            error_msg = "; ".join(validation_errors)
            raise ValueError(f"Validation errors: {error_msg}")

        # Persist updated job configuration to Redis
        try:
            await redis_service.save_job(job_id, job)
            await logger.info(
                f"Job {job_id} configuration persisted to Redis. Updated fields: {updated_fields}"
            )
        except Exception as e:
            await logger.error(
                f"Failed to persist job {job_id} to Redis: {str(e)}", complete_info=e
            )
            raise Exception(f"Failed to save configuration to Redis: {str(e)}") from e

        if skipped_fields:
            await logger.info(
                f"Job {job_id} patch skipped immutable fields: {', '.join(skipped_fields)}"
            )

        return job

    async def verify_inference(self, request: RequestRegisterUseCase) -> dict[str, Any]:  # noqa: C901
        logger = container.resolve(ILoggerService)
        frame_processor: IFrameProcessingService = container.resolve(IFrameProcessingService)

        try:
            # Validate regions first
            regions = request.regions or []
            _validate_regions_for_register_and_verify(request.kpi_name, regions, frame_processor)
            # Resolve and save dynamic config
            has_custom = bool(request.config_file_content or request.config_file_url)
            tracker_type, merged_config = await self._handle_dynamic_config(request)
            if has_custom:
                self._save_custom_tracker_yaml(merged_config, "verification")
            else:
                import os

                custom_yaml_path = "services/model/cfgs/trackers/custom_tracker_verification.yaml"
                if os.path.exists(custom_yaml_path):
                    try:
                        os.remove(custom_yaml_path)
                    except Exception:
                        pass
            request.track_name = tracker_type

            # Use ModelRegistry with model_id from request or auto-select from KPI
            from core.container import create_model_service_with_id

            if is_youtube_url(request.url):
                youtube_url = get_youtube_stream_url(request.url)
                if not youtube_url:
                    raise ValueError(f"invalid youtube url:{request.url}")
                request.url = youtube_url

            # Create service with specific model
            model_service = create_model_service_with_id()
            await model_service.initialize_model(
                job_id="verification",
                kpi_name=request.kpi_name,
                models=request.model_infos,
                tag=request.tag,
                is_global_reid=request.is_global_reid,
                track_name=request.track_name,
            )
            # Verification endpoint: return annotated frame as base64 (jobs use ndarray-only processing).
            result = await frame_processor.process_video_frame(
                url=request.url,
                regions=regions,
                tag=request.tag,
                snapshots_at_tag=request.snapshots_at_tag,
                tag_at_reid=request.tag_at_reid,
                model_service=model_service,
                kpi_name=request.kpi_name,
                return_base64=True,
            )

            if result is None:
                raise RuntimeError("Frame processing failed")
            if not result.processing_success:
                raise RuntimeError(result.error_message or "Frame processing failed")

            # Extract the information needed for the API response
            kpi_results = result.kpi_results

            for detection in result.detection:
                if detection.get(MASK, None) is not None:
                    detection[MASK] = None
                elif detection.get(KEYPOINTS, None) is not None:
                    detection[KEYPOINTS] = None

            tag = request.tag
            verification_result = {
                "frame_base64": result.frame_base64,
                "total_detections": result.total_detections,
                "detections": result.detection,
                "regions_count": result.regions_count,
                "kpi_summary": kpi_results.model_dump() if kpi_results else {},
                "timestamp": result.timestamp,
                "supported_kpis": "",
                "tag": tag,
                "tracker_model_used": tracker_type,
                "tracker_params_used": merged_config,
            }

            await logger.info(f"Verification completed for camera {request.camera_id}")
            return verification_result

        except Exception as e:
            await logger.error(f"Verification failed: {str(e)}")
            raise
        finally:
            import os

            custom_yaml_path = "services/model/cfgs/trackers/custom_tracker_verification.yaml"
            if os.path.exists(custom_yaml_path):
                try:
                    os.remove(custom_yaml_path)
                except Exception:
                    pass

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        job_manager: IBackgroundJobManager = container.resolve(IBackgroundJobManager)
        model_service = container.resolve(IModelService)
        redis_service: IRedisService = container.resolve(IRedisService)
        logger = container.resolve(ILoggerService)

        try:
            status = await job_manager.get_job_status(job_id)
            job_data = await redis_service.get_job(job_id)
            model_info = model_service.get_model_info()
            if job_data:
                job_data.job_status = status.job_status if status else JobStatus.STOPPED.value
                dumped = job_data.model_dump()
                if not dumped.get("tracker_config"):
                    dumped["tracker_config"] = self._get_default_tracker_config(
                        job_data.tracker_model_used or "bytetrack"
                    )
                return {
                    **dumped,
                    "model_name": model_info.get("model_name"),
                    "device": model_info.get("device"),
                }

            return {
                "job_id": job_id,
                "job_status": JobStatus.ERROR.value,
                "error_message": "Job not found",
            }
        except Exception as e:
            await logger.error(f"Error retrieving job status for {job_id}: {str(e)}")
            raise e

    async def get_health_status(self) -> dict[str, Any]:
        logger = container.resolve(ILoggerService)
        try:
            # Check Redis connection
            await redis_client.set("test", "test")
            redis_connected = await redis_client.exists("test")

            # Get running jobs count (Redis + local; matches /jobs/running)
            running_jobs = await self.get_running_jobs()

            # Get system info
            memory_usage = psutil.virtual_memory().used / 1024 / 1024  # MB
            uptime = time.time() - self.start_time

            status = {
                "redis_connected": redis_connected,
                "total_running_jobs": len(running_jobs),
                "api_status": "healthy" if redis_connected else "degraded",
                "uptime_seconds": uptime,
                "memory_usage_mb": memory_usage,
            }

            await logger.info("Health status checked successfully")
            return status
        except Exception as e:
            await logger.error(f"Health status check failed: {str(e)}")
            raise

    async def get_all_jobs(self, status: str) -> dict[str, Any]:
        """Get all jobs with optional status filtering."""
        logger: ILoggerService = container.resolve(ILoggerService)
        redis_service: IRedisService = container.resolve(IRedisService)
        job_manager: IBackgroundJobManager = container.resolve(IBackgroundJobManager)

        try:
            # Get all jobs tracked by background job manager (uses in-memory dictionary, not Redis pattern matching)
            all_job_ids = list(job_manager.job_statuses.keys())

            running_jobs = await job_manager.get_all_running_jobs() or []
            running_job_ids = {job.job_id for job in running_jobs}

            # Retrieve full job details for each job from Redis using exact key lookup
            all_jobs = []
            for jid in all_job_ids:
                job_data = await redis_service.get_job(jid)
                if job_data:
                    # Align with in-memory runners before filtering (Redis can still say "paused").
                    if jid in running_job_ids:
                        job_data.job_status = JobStatus.RUNNING.value
                    all_jobs.append(job_data)
                elif jid in running_job_ids:
                    # Fallback: job is running locally but Redis has no record (save failed, etc.).
                    fallback = job_manager.job_statuses.get(jid)
                    if fallback is not None:
                        all_jobs.append(fallback)

            # Filter by status if specified (after merging runtime RUNNING overlay)
            if status.lower() != "all":
                all_jobs = [job for job in all_jobs if job.job_status == status.lower()]

            # Enrich job data with full runtime snapshot where available
            enriched_jobs = []
            for job in all_jobs:
                job_dict = job.model_dump(exclude={"numpy_frame", "raw_frame", "last_detections"})
                if job.job_id in running_job_ids:
                    job_dict["job_status"] = JobStatus.RUNNING.value
                enriched_jobs.append(job_dict)

            await logger.info(f"Retrieved {len(enriched_jobs)} jobs with status filter: {status}")

            return {
                "total_jobs": len(enriched_jobs),
                "status_filter": status,
                "jobs": enriched_jobs,
            }
        except Exception as e:
            await logger.error(f"Error retrieving all jobs: {str(e)}")
            raise

    async def get_running_jobs(self) -> list[JobConfig]:
        """
        Return jobs in RUNNING state. Combines this worker's BackgroundJobManager with
        Redis so listing works under multiple API workers (register on worker A, GET on B).
        If a job exists locally, local status (including ERROR after a crashed child) wins
        over a stale Redis RUNNING flag.
        """
        redis_service: IRedisService = container.resolve(IRedisService)
        job_manager: IBackgroundJobManager = container.resolve(IBackgroundJobManager)
        logger: ILoggerService = container.resolve(ILoggerService)

        by_id: dict[str, JobConfig] = {}
        for jc in await job_manager.get_all_running_jobs() or []:
            if jc.job_id:
                by_id[jc.job_id] = jc

        try:
            redis_jobs = await redis_service.get_all_jobs()
        except Exception as e:
            await logger.error(f"get_running_jobs: Redis get_all_jobs failed: {e}")
            return list(by_id.values())

        for rj in redis_jobs:
            if not rj.job_id or rj.job_status != JobStatus.RUNNING.value:
                continue
            jid = rj.job_id
            if jid in job_manager.job_statuses:
                local = await job_manager.get_background_job(jid)
                if local and local.job_status == JobStatus.RUNNING.value:
                    by_id[jid] = local
                continue
            if jid not in by_id:
                by_id[jid] = rj

        return list(by_id.values())

    def _build_stages_for_job(self, job: JobConfig) -> list[dict[str, Any]]:
        """Build stages structure from KPI and model information for a job."""
        from services.model.model_registry import model_registry

        kpi_mappings = model_registry.get_model_id_for_kpi(job.kpi_name)
        if not kpi_mappings:
            return []

        stages_dict: dict[int, dict[str, Any]] = {}
        for mapping in kpi_mappings:
            for model_stage in mapping.model_stages:
                stage_num = model_stage.stage

                if stage_num not in stages_dict:
                    stages_dict[stage_num] = {
                        "stageId": f"stage_{stage_num}",
                        "stageName": f"Stage {stage_num}",
                        "models": [],
                    }

                # Check if this model is used in the job
                for model_info in job.model_infos or []:
                    if model_info.model_id == model_stage.model_id:
                        model_entry = {
                            "modelId": model_stage.model_id,
                            "modelName": model_stage.model_id,
                            "order": model_info.order,
                            "leadBy": model_info.lead_by,
                            "classNames": [],
                        }
                        stages_dict[stage_num]["models"].append(model_entry)

        return [stages_dict[stage_num] for stage_num in sorted(stages_dict.keys())]

    def _build_region_coordinates(self, region: Region) -> Any:
        """Return coordinates payload for a region (rect dict, points list, or None)."""
        if region.coordinates:
            return {
                "x_min": region.coordinates.x_min,
                "y_min": region.coordinates.y_min,
                "x_max": region.coordinates.x_max,
                "y_max": region.coordinates.y_max,
            }
        if region.points:
            return [{"x": p.x, "y": p.y} for p in region.points]
        if region.line_points:
            return [{"x": p.x, "y": p.y} for p in region.line_points]
        return None

    def _build_regions_for_job(self, job: JobConfig) -> list[dict[str, Any]]:
        """Build regions structure from a job's regions."""
        if not job.regions:
            return []

        regions = []
        for idx, region in enumerate(job.regions):
            region_entry = {
                "regionId": f"region_{idx}",
                "regionName": region.name,
                "type": region.type.value if hasattr(region.type, "value") else str(region.type),
                "coordinates": self._build_region_coordinates(region),
            }
            regions.append(region_entry)
        return regions

    async def get_job_details(self, job_id: str) -> dict[str, Any]:
        """Get complete job configuration details with stages, models, and regions."""
        logger: ILoggerService = container.resolve(ILoggerService)
        redis_service: IRedisService = container.resolve(IRedisService)
        job_manager: IBackgroundJobManager = container.resolve(IBackgroundJobManager)

        try:
            # Get job from Redis
            job = await redis_service.get_job(job_id)

            if not job:
                await logger.warning(f"Job {job_id} not found")
                return {"job_id": job_id, "error": "Job not found"}

            # Get current status from job manager
            running_job = await job_manager.get_background_job(job_id)
            if running_job:
                job.job_status = running_job.job_status
                job.started_at = running_job.started_at
                job.frames_processed = running_job.frames_processed
                job.current_kpi_value = running_job.current_kpi_value

            # Build stages structure from KPI and model information
            stages: list[dict[str, Any]] = []
            try:
                stages = self._build_stages_for_job(job)
            except Exception as e:
                await logger.warning(f"Could not build stages for job {job_id}: {str(e)}")

            # Build regions structure
            regions = self._build_regions_for_job(job)

            # Build response
            job_details = {
                "jobId": job.job_id,
                "jobName": f"{job.camera_id}_{job.location_id}_{job.kpi_name}",
                "cameraId": job.camera_id,
                "locationId": job.location_id,
                "kpiName": job.kpi_name,
                "url": job.url,
                "urlType": job.url_type,
                "tag": job.tag,
                "jobStatus": job.job_status,
                "createdAt": job.created_at,
                "startedAt": job.started_at,
                "framesProcessed": job.frames_processed,
                "currentKpiValue": job.current_kpi_value,
                "stages": stages,
                "regions": regions,
                "trackerModelUsed": job.tracker_model_used,
                "trackerParamsUsed": job.tracker_config
                or self._get_default_tracker_config(job.tracker_model_used or "bytetrack"),
                "modelInfos": [
                    {"modelId": m.model_id, "order": m.order, "leadBy": m.lead_by}
                    for m in (job.model_infos or [])
                ],
            }

            await logger.info(f"Retrieved details for job {job_id}")
            return job_details
        except Exception as e:
            await logger.error(f"Error retrieving job details for {job_id}: {str(e)}")
            raise

    async def get_activity_types(self) -> dict[str, Any]:
        """Get all available activity types (KPIs) for job creation."""
        logger: ILoggerService = container.resolve(ILoggerService)

        try:
            from constants.kpi_names_constant import (
                KPI_NAME_ACTIVITY_MATCHING_PE,
                KPI_NAME_LINE_PASSING_COUNT,
                KPI_NAME_POSE_ATTENDANCE,
                KPI_NAME_ROI_REGION_DEPENDENCY_OBJECT_DETECTION,
            )

            # Build activity types list
            activity_types = [
                # {
                #     "activityTypeId": KPI_NAME_GENERAL_OBJECT_DETECTION,
                #     "name": "General Object Detection",
                #     "description": "Detect and classify objects in images",
                # },
                # {
                #     "activityTypeId": KPI_NAME_GENERAL_INSTANCE_SEGMENTATION,
                #     "name": "Instance Segmentation",
                #     "description": "Detect and segment individual objects",
                # },
                # {
                #     "activityTypeId": KPI_NAME_ROI_OBJECT_DETECTION,
                #     "name": "ROI Object Detection",
                #     "description": "Detect objects within regions of interest",
                # },
                # {
                #     "activityTypeId": KPI_NAME_ROI_COUNT_OBJECT_DETECTION,
                #     "name": "ROI Count Detection",
                #     "description": "Count objects within regions",
                # },
                # {
                #     "activityTypeId": KPI_NAME_GENERAL_POSE_ESTIMATION,
                #     "name": "Pose Estimation",
                #     "description": "Estimate human body poses",
                # },
                {
                    "activityTypeId": KPI_NAME_ACTIVITY_MATCHING_PE,
                    "name": "Activity Matching",
                    "description": "Match detected actions with activity types",
                },
                {
                    "activityTypeId": KPI_NAME_LINE_PASSING_COUNT,
                    "name": "Line Passing Count",
                    "description": "Count objects crossing a line",
                },
                {
                    "activityTypeId": KPI_NAME_POSE_ATTENDANCE,
                    "name": "Pose Attendance",
                    "description": "Track attendance via pose detection",
                },
                # {
                #     "activityTypeId": KPI_NAME_BLINK_DROWSINESS,
                #     "name": "Drowsiness Detection",
                #     "description": "Detect eye blinks and drowsiness",
                # },
                # {
                #     "activityTypeId": KPI_NAME_HEATMAP,
                #     "name": "Heatmap",
                #     "description": "Generate heatmap of object activity",
                # },
                # {
                #     "activityTypeId": KPI_ROI_COUNT_TIME_OBJECT_DETECTION,
                #     "name": "ROI Count Over Time",
                #     "description": "Count objects in ROI with time tracking",
                # },
                {
                    "activityTypeId": KPI_NAME_ROI_REGION_DEPENDENCY_OBJECT_DETECTION,
                    "name": "Region Dependency Detection",
                    "description": "Detect objects with region dependencies",
                },
                # {
                #     "activityTypeId": KPI_NAME_MULTISTAGE,
                #     "name": "Multistage",
                #     "description": "Multi-stage processing pipeline",
                # },
                # {
                #     "activityTypeId": KPI_NAME_FACE_RECOGNITION,
                #     "name": "Face Recognition",
                #     "description": "Recognize and identify faces",
                # },
                # {
                #     "activityTypeId": KPI_NAME_INTERACTION_DETECTION_PE,
                #     "name": "Interaction Detection",
                #     "description": "Detect human interactions",
                # },
            ]

            await logger.info(f"Retrieved {len(activity_types)} activity types")

            return {"total_activity_types": len(activity_types), "activity_types": activity_types}
        except Exception as e:
            await logger.error(f"Error retrieving activity types: {str(e)}")
            raise

    async def get_stages_by_activity(self, activity_type_id: str) -> dict[str, Any]:
        """Get stages and models for a specific activity type."""
        logger: ILoggerService = container.resolve(ILoggerService)

        try:
            from services.model.model_registry import model_registry

            # Get model mappings for this KPI
            kpi_mappings = model_registry.get_model_id_for_kpi(activity_type_id)

            if not kpi_mappings:
                await logger.warning(f"No stages found for activity type '{activity_type_id}'")
                return {
                    "activityTypeId": activity_type_id,
                    "error": f"No stages found for activity type '{activity_type_id}'",
                }

            # Group models by stage
            stages_dict: dict[int, dict[str, Any]] = {}

            for mapping in kpi_mappings:
                for model_stage in mapping.model_stages:
                    stage_num = model_stage.stage

                    if stage_num not in stages_dict:
                        stages_dict[stage_num] = {
                            "stageId": f"stage_{stage_num}",
                            "stageName": f"Stage {stage_num}",
                            "stageOrder": stage_num,
                            "models": [],
                        }

                    model_entry: dict[str, Any] = {
                        "modelId": model_stage.model_id,
                        "modelName": model_stage.model_id,
                        "stageClass": str(model_stage.stage_class.__name__)
                        if model_stage.stage_class
                        else None,
                        "classNames": [],
                    }
                    stages_dict[stage_num]["models"].append(model_entry)

            # Convert to sorted list by stage
            stages = [stages_dict[stage_num] for stage_num in sorted(stages_dict.keys())]

            await logger.info(
                f"Retrieved {len(stages)} stages for activity type {activity_type_id}"
            )

            return {
                "activityTypeId": activity_type_id,
                "totalStages": len(stages),
                "stages": stages,
            }
        except Exception as e:
            await logger.error(f"Error retrieving stages for {activity_type_id}: {str(e)}")
            raise

    async def get_model_classes(self, model_id: str) -> dict[str, Any]:
        """Get class names for a specific model."""
        logger: ILoggerService = container.resolve(ILoggerService)

        try:
            from constants.schemas.hosted_models import MODEL_HOSTED
            from services.model.model_registry import model_registry

            class_names = []

            # First, try to get from loaded model
            loaded_model = model_registry.get_model(model_id)
            if loaded_model:
                # YOLO models have a names dictionary
                if hasattr(loaded_model, "names") and loaded_model.names:
                    class_names = list(loaded_model.names.values())

            # If not loaded, fallback to hosted models config
            if not class_names:
                raw_models = MODEL_HOSTED.get("models", [])
                models_iter = (
                    cast(list[dict[str, Any]], raw_models) if isinstance(raw_models, list) else []
                )
                for model_config in models_iter:
                    name_field = str(model_config.get("model_name", ""))
                    if model_config.get("model_name") == model_id or model_id in name_field:
                        raw_classes = model_config.get("classes", [])
                        class_names = (
                            [str(c) for c in raw_classes] if isinstance(raw_classes, list) else []
                        )
                        break

            # If still no classes, use common COCO classes as fallback for YOLO models
            if not class_names:
                if (
                    "general" in model_id.lower()
                    or "pt" in model_id.lower()
                    or "v9" in model_id.lower()
                    or "v10" in model_id.lower()
                    or "v11" in model_id.lower()
                ):
                    class_names = [
                        "person",
                        "bicycle",
                        "car",
                        "motorbike",
                        "aeroplane",
                        "bus",
                        "train",
                        "truck",
                        "boat",
                        "traffic light",
                        "fire hydrant",
                        "stop sign",
                        "parking meter",
                        "bench",
                        "bird",
                        "cat",
                        "dog",
                        "horse",
                        "sheep",
                        "cow",
                        "elephant",
                        "bear",
                        "zebra",
                        "giraffe",
                        "backpack",
                        "umbrella",
                        "handbag",
                        "tie",
                        "suitcase",
                        "frisbee",
                        "skis",
                        "snowboard",
                        "sports ball",
                        "kite",
                        "baseball bat",
                        "baseball glove",
                        "skateboard",
                        "surfboard",
                        "tennis racket",
                        "bottle",
                        "wine glass",
                        "cup",
                        "fork",
                        "knife",
                        "spoon",
                        "bowl",
                        "banana",
                        "apple",
                        "sandwich",
                        "orange",
                        "broccoli",
                        "carrot",
                        "hot dog",
                        "pizza",
                        "donut",
                        "cake",
                        "chair",
                        "sofa",
                        "pottedplant",
                        "bed",
                        "diningtable",
                        "toilet",
                        "tvmonitor",
                        "laptop",
                        "mouse",
                        "remote",
                        "keyboard",
                        "cell phone",
                        "microwave",
                        "oven",
                        "toaster",
                        "sink",
                        "refrigerator",
                        "book",
                        "clock",
                        "vase",
                        "scissors",
                        "teddy bear",
                        "hair drier",
                        "toothbrush",
                    ]

            # Build response with class details
            classes_list = []
            for idx, class_name in enumerate(class_names):
                classes_list.append({"classId": str(idx), "className": class_name, "index": idx})

            await logger.info(f"Retrieved {len(classes_list)} classes for model {model_id}")

            return {"modelId": model_id, "totalClasses": len(classes_list), "classes": classes_list}
        except Exception as e:
            await logger.error(f"Error retrieving model classes for {model_id}: {str(e)}")
            raise e

    async def _fetch_global_embeddings_from_grpc(self, _tag_at_reid: str) -> list[dict[str, Any]]:
        """
        Fetch global embeddings from pgvector via gRPC based on tag_at_reid.

        This is a placeholder method. The actual gRPC implementation will be added later.

        Args:
            _tag_at_reid: Tag to filter global embeddings (e.g., "employee", "visitor")

        Returns:
            List of dictionaries with format:
            [
                {
                    "global_id": "G1",
                    "embedding": [512-dimensional vector],
                    "object_name": "person"
                },
                ...
            ]
        """
        logger = container.resolve(ILoggerService)

        # TEMPORARY TEST CODE: Generate random embeddings with class name "Azan"
        import numpy as np

        # Generate test data with random embeddings
        num_test_entries = 5  # Number of test entries to generate
        test_global_embeddings_data = []

        for _i in range(num_test_entries):
            # Generate random UUID for global_id
            test_global_id = str(uuid.uuid4())

            # Generate random embedding (512-dimensional)
            test_embedding = np.random.randn(512).tolist()

            # Create entry with class name "Azan"
            test_entry = {
                "global_id": test_global_id,  # G_N / G_UNKNOWNW
                "embedding": test_embedding,
                "object_name": "face",
            }
            test_global_embeddings_data.append(test_entry)

        await logger.info(
            f"[TEST MODE] Generated {len(test_global_embeddings_data)} random embeddings with class name 'Azan'"
        )
        return test_global_embeddings_data

        # ACTUAL gRPC CALL (COMMENTED OUT FOR TESTING)
        # TODO: Implement actual gRPC call
        # Example:
        # from services.grpc.grpc_client_service import grpc_client_service
        # embeddings_data = await grpc_client_service.get_global_embeddings_by_tag(tag_at_reid)
        # return embeddings_data

        # await logger.warning(f"gRPC call not yet implemented. Returning empty global embeddings list for tag: {tag_at_reid}")
        # return []


# static class and static funtion here store global cache assume it is faiss gpu enabled need to pass N(1-N) and group embeddings according to tag_at_reid
# class GlobalCacheManager:
# @staticmethod
# async def initialize_global_cache(tag:str):
#     """Retrieve global cache information. from Employee_Embeddngs table. get
#                 only fields embedding_id and employee_id and embedding vector. and store in redis in list key 'global_employee_embeddings'
#     """
#     from services.grpc.grpc_client_service import grpc_client_service
#     from host.datasource.redis_client import redis_client
#     embeddings_list=await grpc_client_service.get_all_employee_embeddings_by_tag(tag)
#     if not embeddings_list:
#         return
#     #store in faiss

# if embeddings is global so dont delete them. if embedding is local/noise so save/add them to cache until less<N if more so delete old and add new in I cache manager srvices
