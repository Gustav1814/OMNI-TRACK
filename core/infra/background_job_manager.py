# services/background_job_manager.py (Updated)
import asyncio
import base64
import json
import multiprocessing
import os
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, cast

import cv2
import numpy as np

from constants.detections_constant import SHARED_PATH
from constants.job_constant.background_job_constant import VIDEO_DATE_FORMAT
from constants.kpis.counting_names import NOT_PASSED, PASSED, TOTAL
from constants.region_names import (
    CAMERA_ID,
    FRAME_COUNT,
    JOB_ID,
    KPI_NAME,
    LOCATION_ID,
    PRESERVED_DATA,
)
from core.container import container
from dtos.common.constants.api_config import API_NAME
from dtos.common.enums.job_status import JobStatus
from dtos.common.enums.url_type import URLType
from services.common.models.job_models import JobConfig
from services.common.models.yolo_models import KPIResult, ROICountTime, TagCount
from services.core.interfaces.ibackground_job_manager import IBackgroundJobManager
from services.core.interfaces.ilogger_service import ILoggerService
from services.db_sources.redis_repos.iredis_service import IRedisService
from services.inferenced_services.interfaces.iframe_processing_service import (
    IFrameProcessingService,
)
from services.interfaces.imodel_service import IModelService

_FRAME_SEGMENT_THRESHOLD = 30


def _serialize_stream_summary_for_proxy(summary: KPIResult | dict[str, Any] | None) -> Any:
    """Flatten summary for multiprocessing proxy + SSE (no defaultdict / non-JSON types)."""
    if summary is None:
        return None
    if isinstance(summary, KPIResult):
        return summary.model_dump(mode="json")
    try:
        return json.loads(json.dumps(summary, default=str))
    except (TypeError, ValueError):
        return None


async def _merge_worker_job_summary(job_config: JobConfig, kpi_result: KPIResult) -> None:
    """Same merging rules as ``BackgroundJobManager.adjust_summary``, on subprocess ``JobConfig``."""
    if kpi_result.tag_counts is not None and len(kpi_result.tag_counts) > 0:
        existing = job_config.summary
        if existing is None:
            job_config.summary = kpi_result
        elif not isinstance(existing, KPIResult):
            job_config.summary = kpi_result
        else:
            for item_name in kpi_result.tag_counts:
                value = kpi_result.tag_counts[item_name]
                is_already_stored = existing.tag_counts.get(item_name, None)
                if is_already_stored is not None and "count" not in kpi_result.kpi_name:
                    existing.tag_counts[
                        item_name
                    ].current_unique_objects_in_region += value.current_unique_objects_in_region
                else:
                    existing.tag_counts[item_name] = value
    elif kpi_result.roi_dependent is not None and len(kpi_result.roi_dependent) > 0:
        roi_dependent: dict[int, ROICountTime] = kpi_result.roi_dependent
        job_config.summary = BackgroundJobManager.aggregate_roi_kpis(roi_dependent)
    elif kpi_result.line_tag_counts is not None and len(kpi_result.line_tag_counts) > 0:
        kpis_line_tag: dict[int, TagCount] = kpi_result.line_tag_counts
        job_config.summary = await BackgroundJobManager.line_counter(kpis_line_tag)
    elif kpi_result.general_ob is not None and len(kpi_result.general_ob) > 0:
        job_config.summary = await BackgroundJobManager.count_flat_list(kpi_result.general_ob)
    elif kpi_result.roi_count is not None and len(kpi_result.roi_count) > 0:
        roi_count = kpi_result.roi_count
        job_config.summary = await BackgroundJobManager.count_flat_list(list(roi_count.values()))
    else:
        job_config.summary = kpi_result


def _kpi_result_from_proxy_value(raw: Any) -> KPIResult | None:
    """Coerce ``last_frame_result`` from the worker proxy (JSON dict) into ``KPIResult`` for KPIs."""
    if raw is None:
        return None
    if isinstance(raw, KPIResult):
        return raw
    if isinstance(raw, dict):
        try:
            return cast(KPIResult, KPIResult.model_validate(raw))
        except Exception:
            return None
    return None


def _apply_last_frame_result_from_proxy(job_data: JobConfig, raw: Any) -> None:
    job_data.last_frame_result = _kpi_result_from_proxy_value(raw)


def _sync_job_config_from_status_proxy(job_data: JobConfig, status_proxy: Any) -> None:
    job_data.frames_processed = status_proxy.get("frames_processed", job_data.frames_processed)
    _fn = status_proxy.get("frames_processed")
    if _fn is None:
        _fn = status_proxy.get("frame_number")
    if _fn is not None:
        job_data.frame_number = _fn
    job_data.current_kpi_value = status_proxy.get("current_kpi_value", job_data.current_kpi_value)
    job_data.current_frame = status_proxy.get("current_frame", job_data.current_frame)
    job_data.last_processing_time = status_proxy.get(
        "last_processing_time", job_data.last_processing_time
    )
    job_data.job_status = status_proxy.get("job_status", job_data.job_status)
    job_data.error_message = status_proxy.get("error_message", job_data.error_message)
    job_data.last_detections = status_proxy.get("last_detections", job_data.last_detections)
    job_data.summary = status_proxy.get("summary", job_data.summary)
    _apply_last_frame_result_from_proxy(job_data, status_proxy.get("last_frame_result"))


def _hydrate_numpy_frames_from_proxy(job_data: JobConfig, status_proxy: Any) -> None:
    # current_bytes = status_proxy.get("current_frame_bytes")
    numpy_frame = status_proxy.get("numpy_frame")
    # if current_bytes:
    #     frame_array = cv2.imdecode(np.frombuffer(current_bytes, np.uint8), cv2.IMREAD_COLOR)
    #     if frame_array is not None:
    #         job_data.numpy_frame = frame_array
    #         job_data.raw_frame = frame_array.copy()
    if numpy_frame is not None:
        job_data.numpy_frame = numpy_frame
        job_data.raw_frame = numpy_frame
    if job_data.numpy_frame is None and job_data.current_frame:
        try:
            raw = base64.b64decode(job_data.current_frame, validate=False)
            frame_array = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if frame_array is not None:
                job_data.numpy_frame = frame_array
                if job_data.raw_frame is None:
                    job_data.raw_frame = frame_array.copy()
        except Exception:
            pass


async def _read_cap_frame(
    cap: Any,
    url: str,
    job_config: JobConfig,
    job_id: str,
    logger: ILoggerService,
) -> tuple[Any, np.ndarray | None, bool]:
    """Return ``(cap, frame, should_break)``. ``frame`` is ``None`` when RTSP reconnected (retry loop)."""
    ret, frame = cap.read()
    if ret:
        return cap, frame, False
    if job_config.url_type == URLType.RTSP.value:
        await logger.warning(f"RTSP stream {job_id} disconnected, attempting to reconnect...")
        cap.release()
        await asyncio.sleep(5)
        return cv2.VideoCapture(url), None, False
    return cap, None, True


async def _roll_segment_writer_if_needed(
    segment_writer: Any,
    shared_folder_path: str | None,
    job_config: JobConfig,
    kpi_name: str,
    camera_id: str,
    location_id: str,
    fps: float,
    logger: ILoggerService,
) -> tuple[Any, str | None]:
    if segment_writer is None or (
        job_config.frame_number % _FRAME_SEGMENT_THRESHOLD == 0 and job_config.frame_number != 0
    ):
        if segment_writer is not None:
            segment_writer.release()
        if shared_folder_path is None:
            shared_folder_path = BackgroundJobManager._get_shared_folder()
        track_name = getattr(job_config, "track_name", "bytetrack")
        timestamp = datetime.now(UTC).strftime(VIDEO_DATE_FORMAT)
        video_filename = (
            f"{shared_folder_path}/kpis~{kpi_name}~{camera_id}~{location_id}~{track_name}~{timestamp}~"
            f"{job_config.frame_number}_{_FRAME_SEGMENT_THRESHOLD + job_config.frame_number}.mp4"
        )
        fourcc = cv2.VideoWriter_fourcc(*"avc1")  # type: ignore[attr-defined]  # H.264 for browser playback
        frame_w = job_config.width or 640
        frame_h = job_config.height or 480
        segment_writer = cv2.VideoWriter(video_filename, fourcc, fps, (frame_w, frame_h))
        await logger.info(f"Started recording new clip: {video_filename}")

    if shared_folder_path is None:
        shared_folder_path = BackgroundJobManager._get_shared_folder()

    return segment_writer, shared_folder_path


async def _apply_successful_processing(
    *,
    job_config: JobConfig,
    status_proxy: dict[str, Any],
    processing_result: Any,
    frame: np.ndarray,
    segment_writer: Any,
    redis_service: IRedisService,
    logger: ILoggerService,
    camera_id: str,
    location_id: str,
    kpi_name: str,
    num_of_frame_per_sec: int,
) -> None:
    job_config.frame_number += 1
    kpi_result = processing_result.kpi_results
    status_proxy["frames_processed"] = job_config.frame_number
    status_proxy["frame_number"] = job_config.frame_number
    ann_frame = processing_result.annotated_frame
    visual_frame = ann_frame if ann_frame is not None else frame

    if kpi_result is not None:
        await _merge_worker_job_summary(job_config, kpi_result)
        kpi_blob = kpi_result.model_dump(mode="json")
        status_proxy["current_kpi_value"] = kpi_blob
        status_proxy["last_frame_result"] = kpi_blob
        status_proxy["summary"] = _serialize_stream_summary_for_proxy(job_config.summary)

    # Registered jobs: no base64 in the worker proxy (preview uses current_frame_bytes in get_background_job).
    status_proxy["current_frame"] = ""
    status_proxy["last_processing_time"] = processing_result.timestamp
    status_proxy["job_status"] = JobStatus.RUNNING.value
    status_proxy["last_detections"] = processing_result.detection

    encoded_frame = b""
    # if visual_frame is not None:
    #     try:
    #         ret_frame, buffer = cv2.imencode(".jpg", visual_frame)
    #         if ret_frame:
    #             encoded_frame = buffer.tobytes()
    #     except Exception:
    #         encoded_frame = b""

    status_proxy["current_frame_bytes"] = encoded_frame
    status_proxy["numpy_frame"] = visual_frame.copy()

    if segment_writer is not None and visual_frame is not None:
        segment_writer.write(visual_frame)

    if job_config.frame_number % num_of_frame_per_sec == 0 and kpi_result is not None:
        await redis_service.save_kpi_log(
            kpi_name,
            kpi_result,
            camera_id,
            location_id,
            logger,
            detection_log_ttl_seconds=job_config.redis_detection_log_ttl_seconds,
        )


def _ensure_capture_frame_size(cap: Any, job_config: JobConfig) -> None:
    if job_config.width is None or job_config.height is None:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
        job_config.width = job_config.width or width
        job_config.height = job_config.height or height


async def _process_frame_with_services(
    *,
    frame: np.ndarray,
    job_id: str,
    job_config: JobConfig,
    status_proxy: dict[str, Any],
    segment_writer: Any,
    shared_folder_path: str | None,
    frame_processor: IFrameProcessingService,
    model_service: IModelService,
    redis_service: IRedisService,
    logger: ILoggerService,
    models: list[Any] | None,
    regions: list[Any] | None,
    kpi_name: str,
    camera_id: str,
    location_id: str,
    tag: str,
    snapshots_at_tag: str,
    tag_at_reid: str | None,
    is_global_reid: bool,
    num_of_frame_per_sec: int,
    key_args: dict[str, Any],
    fps: float,
) -> tuple[Any, str | None]:
    segment_writer, shared_folder_path = await _roll_segment_writer_if_needed(
        segment_writer,
        shared_folder_path,
        job_config,
        kpi_name,
        camera_id,
        location_id,
        fps,
        logger,
    )

    BackgroundJobManager.get_key_args(
        key_args,
        regions,
        kpi_name,
        loc_id=job_config.location_id,
        cam_id=job_config.camera_id,
        shared_path=shared_folder_path or "",
        models=models,
    )

    last_frame_result = _kpi_result_from_proxy_value(status_proxy.get("last_frame_result"))
    frame_extra_kwargs: dict[str, Any] = {
        PRESERVED_DATA: last_frame_result,
        "all_key_args": {
            **key_args,
            FRAME_COUNT: job_config.frame_number,
            JOB_ID: job_id,
            CAMERA_ID: camera_id,
            LOCATION_ID: location_id,
            KPI_NAME: kpi_name,
        },
    }
    try:
        processing_result = await frame_processor.process_frame_complete(
            frame=frame,
            regions=regions or [],
            model_service=model_service,
            kpi_name=kpi_name,
            draw_annotations=True,
            return_base64=False,
            tag_at_reid=tag_at_reid,
            _is_global_reid=is_global_reid,
            tag=tag,
            snapshots_at_tag=snapshots_at_tag or "",
            **frame_extra_kwargs,
        )

        if processing_result.processing_success:
            await _apply_successful_processing(
                job_config=job_config,
                status_proxy=status_proxy,
                processing_result=processing_result,
                frame=frame,
                segment_writer=segment_writer,
                redis_service=redis_service,
                logger=logger,
                camera_id=camera_id,
                location_id=location_id,
                kpi_name=kpi_name,
                num_of_frame_per_sec=num_of_frame_per_sec,
            )
        else:
            await logger.error(
                f"Frame processing failed for job {job_id}: {processing_result.error_message}"
            )

    except Exception as processing_error:
        await logger.error(f"Frame processing error for job {job_id}: {str(processing_error)}")

    return segment_writer, shared_folder_path


def _apply_terminal_proxy_job_status(stop_event: Any, status_proxy: dict[str, Any]) -> None:
    if stop_event.is_set():
        status_proxy["job_status"] = JobStatus.CANCELLED.value
        return
    if status_proxy.get("job_status") != JobStatus.ERROR.value:
        status_proxy["job_status"] = JobStatus.STOPPED.value


async def _release_segment_after_loop_and_maybe_delete_job(
    segment_writer: Any,
    stop_event: Any,
    job_id: str,
    redis_service: IRedisService,
    logger: ILoggerService,
) -> None:
    if segment_writer is not None:
        segment_writer.release()

    if not stop_event.is_set():
        await redis_service.delete_job(job_id)
        await logger.info(f"Job {job_id} completed and deleted")


async def _run_frame_loop_until_stopped(
    *,
    job_id: str,
    job_config: JobConfig,
    cap: Any,
    url: str,
    stop_event: Any,
    logger: ILoggerService,
    fps: float,
    status_proxy: dict[str, Any],
    frame_processor: IFrameProcessingService,
    model_service: IModelService,
    redis_service: IRedisService,
    models: list[Any] | None,
    regions: list[Any] | None,
    kpi_name: str,
    camera_id: str,
    location_id: str,
    tag: str,
    snapshots_at_tag: str,
    tag_at_reid: str | None,
    is_global_reid: bool,
    num_of_frame_per_sec: int,
) -> tuple[Any, Any]:
    segment_writer = None
    shared_folder_path = None
    key_args: dict[str, Any] = {}

    while not stop_event.is_set():
        cap, frame, should_break = await _read_cap_frame(cap, url, job_config, job_id, logger)
        if should_break:
            break
        if frame is None:
            continue

        segment_writer, shared_folder_path = await _process_frame_with_services(
            frame=frame,
            job_id=job_id,
            job_config=job_config,
            status_proxy=status_proxy,
            segment_writer=segment_writer,
            shared_folder_path=shared_folder_path,
            frame_processor=frame_processor,
            model_service=model_service,
            redis_service=redis_service,
            logger=logger,
            models=models,
            regions=regions,
            kpi_name=kpi_name,
            camera_id=camera_id,
            location_id=location_id,
            tag=tag,
            snapshots_at_tag=snapshots_at_tag,
            tag_at_reid=tag_at_reid,
            is_global_reid=is_global_reid,
            num_of_frame_per_sec=num_of_frame_per_sec,
            key_args=key_args,
            fps=fps,
        )

        await asyncio.sleep(0.001)

    return cap, segment_writer


async def _run_background_job(
    job_id: str,
    job_config: JobConfig,
    status_proxy: dict[str, Any],
    stop_event: Any,
    redis_service: IRedisService,
    logger: ILoggerService,
    frame_processor: IFrameProcessingService,
) -> None:
    from core.container import create_model_service_with_id
    from host.datasource.redis_client import connect_redis_from_env

    await connect_redis_from_env()

    models = job_config.model_infos
    url = job_config.url
    regions = job_config.regions
    camera_id = job_config.camera_id
    location_id = job_config.location_id
    kpi_name = job_config.kpi_name
    tag = job_config.tag
    snapshots_at_tag = job_config.snapshots_at_tag
    tag_at_reid = job_config.tag_at_reid
    is_global_reid = job_config.is_global_reid
    num_of_frame_per_sec = job_config.num_of_frame_per_sec
    track_name = getattr(job_config, "track_name", "bytetrack")
    if getattr(job_config, "tracker_config", None):
        from config.BaseConfig import config

        config.TRACKER_CONFIG = job_config.tracker_config or {}
        import os

        import yaml

        os.makedirs("services/model/cfgs/trackers", exist_ok=True)
        custom_yaml_path = f"services/model/cfgs/trackers/custom_tracker_{job_id}.yaml"
        with open(custom_yaml_path, "w") as f:
            yaml.dump(job_config.tracker_config, f)

    model_service: IModelService = create_model_service_with_id()
    await model_service.initialize_model(
        job_id,
        kpi_name,
        models or [],
        tag=tag,
        is_global_reid=is_global_reid,
        track_name=track_name,
    )

    cap = None
    segment_writer = None

    try:
        cap = cv2.VideoCapture(url)
        fps = cap.get(cv2.CAP_PROP_FPS) or 15
        _ensure_capture_frame_size(cap, job_config)

        cap, segment_writer = await _run_frame_loop_until_stopped(
            job_id=job_id,
            job_config=job_config,
            cap=cap,
            url=url,
            stop_event=stop_event,
            logger=logger,
            fps=fps,
            status_proxy=status_proxy,
            frame_processor=frame_processor,
            model_service=model_service,
            redis_service=redis_service,
            models=models,
            regions=regions,
            kpi_name=kpi_name,
            camera_id=camera_id,
            location_id=location_id,
            tag=tag,
            snapshots_at_tag=snapshots_at_tag or "",
            tag_at_reid=tag_at_reid,
            is_global_reid=is_global_reid,
            num_of_frame_per_sec=num_of_frame_per_sec,
        )

        await _release_segment_after_loop_and_maybe_delete_job(
            segment_writer, stop_event, job_id, redis_service, logger
        )

    except Exception as e:
        await logger.error(f"Job {job_id} failed: {e}")
        status_proxy["job_status"] = JobStatus.ERROR.value
        status_proxy["error_message"] = str(e)

    finally:
        if cap is not None:
            cap.release()
        if segment_writer is not None:
            segment_writer.release()
        _apply_terminal_proxy_job_status(stop_event, status_proxy)

        # Clean up custom tracker config file
        import os

        custom_yaml_path = f"services/model/cfgs/trackers/custom_tracker_{job_id}.yaml"
        if os.path.exists(custom_yaml_path):
            try:
                os.remove(custom_yaml_path)
            except Exception:
                pass


def _process_job_worker(
    job_id: str,
    job_config_data: dict,
    status_proxy: dict[str, Any],
    stop_event: Any,
) -> None:
    """Worker process entrypoint for a background job."""
    from dotenv import load_dotenv

    load_dotenv()
    from core.container import setup_dependencies

    # Child process has an empty ServiceContainer; app setup only runs in the parent.
    setup_dependencies()

    job_config = JobConfig.model_validate(job_config_data)
    redis_service: IRedisService = container.resolve(IRedisService)
    logger: ILoggerService = container.resolve(ILoggerService)
    frame_processor: IFrameProcessingService = container.resolve(IFrameProcessingService)

    try:
        asyncio.run(
            _run_background_job(
                job_id,
                job_config,
                status_proxy,
                stop_event,
                redis_service,
                logger,
                frame_processor,
            )
        )
    except Exception as e:
        logger_err: ILoggerService = container.resolve(ILoggerService)
        logger_err.sync_error(f"Background process {job_id} crashed: {e}")
        status_proxy["job_status"] = JobStatus.ERROR.value
        status_proxy["error_message"] = str(e)


class BackgroundJobManager(IBackgroundJobManager):
    def __init__(self):
        self.running_jobs: dict[str, Any] = {}
        self.job_stop_events: dict[str, Any] = {}
        self.job_status_proxies: dict[str, Any] = {}
        self.job_statuses: dict[str, JobConfig] = {}
        # Fork-derived children inherit CUDA state from Uvicorn/parent; CUDA must init in fresh Pythons.
        self._mp = multiprocessing.get_context("spawn")
        self.manager = self._mp.Manager()

    async def start_job(self, job_id: str, job_config: JobConfig) -> bool:
        if job_id in self.running_jobs:
            return False

        status_proxy: Any = self.manager.dict(
            {
                "job_id": job_id,
                "frames_processed": 0,
                "frame_number": 0,
                "current_kpi_value": {},
                "current_frame": "",
                "last_processing_time": "",
                "job_status": JobStatus.RUNNING.value,
                "error_message": "",
                "last_detections": None,
                "current_frame_bytes": b"",
                "summary": None,
                "last_frame_result": None,
                "numpy_frame": None,
            }
        )

        stop_event = self._mp.Event()
        process = self._mp.Process(
            target=_process_job_worker,
            args=(job_id, job_config.model_dump(), status_proxy, stop_event),
            daemon=True,
        )
        process.start()

        self.running_jobs[job_id] = process
        self.job_stop_events[job_id] = stop_event
        self.job_status_proxies[job_id] = status_proxy

        job_config.job_status = JobStatus.RUNNING.value
        job_config.started_at = datetime.now(UTC).isoformat()
        job_config.frames_processed = 0
        job_config.summary = None
        job_config.current_frame = ""
        job_config.last_frame_result = None
        job_config.last_processing_time = ""
        self.job_statuses[job_id] = job_config

        return True

    async def get_background_job(self, job_id: str) -> JobConfig | None:
        if job_id not in self.job_statuses:
            return None

        job_data = self.job_statuses[job_id]
        status_proxy = self.job_status_proxies.get(job_id)
        if status_proxy is None:
            return job_data

        _sync_job_config_from_status_proxy(job_data, status_proxy)
        _hydrate_numpy_frames_from_proxy(job_data, status_proxy)

        return job_data

    async def stop_job(self, job_id: str) -> bool:
        process = self.running_jobs.get(job_id)
        stop_event = self.job_stop_events.get(job_id)
        status_proxy = self.job_status_proxies.get(job_id)

        if not process:
            return False

        if stop_event is not None:
            stop_event.set()

        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)

        if status_proxy is not None:
            status_proxy["job_status"] = JobStatus.STOPPED.value

        self.running_jobs.pop(job_id, None)
        self.job_stop_events.pop(job_id, None)
        self.job_status_proxies.pop(job_id, None)

        if job_id in self.job_statuses:
            self.job_statuses[job_id].job_status = JobStatus.STOPPED.value
        return True

    async def get_job_status(self, job_id: str) -> JobConfig | None:
        return await self.get_background_job(job_id)

    async def get_all_running_jobs(self) -> list[JobConfig]:
        jobs: list[JobConfig] = []
        for job_id, job_config in self.job_statuses.items():
            status_proxy = self.job_status_proxies.get(job_id)
            if status_proxy is not None:
                job_config.job_status = status_proxy.get("job_status", job_config.job_status)
            if job_config.job_status == JobStatus.RUNNING.value:
                if status_proxy is not None:
                    job_config.frames_processed = status_proxy.get(
                        "frames_processed", job_config.frames_processed
                    )
                    job_config.current_kpi_value = status_proxy.get(
                        "current_kpi_value", job_config.current_kpi_value
                    )
                jobs.append(job_config)
        return jobs

    async def resume_jobs_from_redis(self) -> bool:
        redis_service: IRedisService = container.resolve(IRedisService)
        logger = container.resolve(ILoggerService)

        try:
            jobs = await redis_service.get_all_jobs()
            for job_data in jobs:
                job_id = job_data.job_id
                if job_id and (
                    job_data.url_type == URLType.RTSP.value
                    or job_data.url_type == URLType.YOUTUBE.value
                    or job_data.url_type == URLType.VIDEO.value
                ):
                    await self.start_job(job_id, job_data)
                    await logger.info(f"Resumed job {job_id} from Redis")
            return True
        except Exception as e:
            await logger.error(f"Failed to resume jobs from Redis: {e}")
            return False

    @staticmethod
    def get_key_args(
        key_args: dict[str, Any],
        regions: list[Any] | None,
        kpi_name: str,
        loc_id: str = "",
        cam_id: str = "",
        shared_path: str = "",
        models: list[Any] | None = None,
    ) -> None:
        if models is None:
            models = []
        if regions is None:
            regions = []
        if kpi_name == "line_passing_count":
            for reg in regions or []:
                if reg.line_points and len(reg.line_points) > 0:
                    key_args[reg.name] = reg.object_moving_direction
        key_args["loc_id"] = loc_id
        key_args["cam_id"] = cam_id
        key_args[SHARED_PATH] = shared_path
        key_args["kpi_name"] = kpi_name
        key_args["model_info"] = models

    @staticmethod
    def _get_shared_folder() -> str:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        services_dir = os.path.dirname(current_dir)
        root_dir = os.path.dirname(services_dir)
        shared_folder_path = os.path.join(root_dir, "shared")
        if not os.path.exists(shared_folder_path):
            os.mkdir(shared_folder_path)
        shared_folder_path = os.path.join(shared_folder_path, API_NAME)
        if not os.path.exists(shared_folder_path):
            os.mkdir(shared_folder_path)
        return shared_folder_path

    async def create_folder_if_not_exist(self) -> str:
        return BackgroundJobManager._get_shared_folder()

    async def adjust_summary(self, job_id: str, kpi_result: KPIResult) -> None:
        if kpi_result.tag_counts is not None and len(kpi_result.tag_counts) > 0:
            existing = self.job_statuses[job_id].summary
            if existing is None:
                self.job_statuses[job_id].summary = kpi_result
            elif not isinstance(existing, KPIResult):
                self.job_statuses[job_id].summary = kpi_result
            else:
                for key in kpi_result.tag_counts:
                    item_name = key
                    value = kpi_result.tag_counts[key]
                    is_already_stored = existing.tag_counts.get(item_name, None)
                    if is_already_stored is not None and "count" not in kpi_result.kpi_name:
                        existing.tag_counts[
                            item_name
                        ].current_unique_objects_in_region += value.current_unique_objects_in_region
                    else:
                        existing.tag_counts[item_name] = value
        elif kpi_result.roi_dependent is not None and len(kpi_result.roi_dependent) > 0:
            roi_dependent: dict[int, ROICountTime] = kpi_result.roi_dependent
            kpi_summary = BackgroundJobManager.aggregate_roi_kpis(roi_dependent)
            self.job_statuses[job_id].summary = kpi_summary
        elif kpi_result.line_tag_counts is not None and len(kpi_result.line_tag_counts) > 0:
            kpis_line_tag: dict[int, TagCount] = kpi_result.line_tag_counts
            kpi_summary = await BackgroundJobManager.line_counter(kpis_line_tag)
            self.job_statuses[job_id].summary = kpi_summary
        elif kpi_result.general_ob is not None and len(kpi_result.general_ob) > 0:
            summary = await self.count_flat_list(kpi_result.general_ob)
            self.job_statuses[job_id].summary = summary
        elif kpi_result.roi_count is not None and len(kpi_result.roi_count) > 0:
            roi_count = kpi_result.roi_count
            summary = await self.count_flat_list(list(roi_count.values()))
            self.job_statuses[job_id].summary = summary

    @staticmethod
    def aggregate_roi_kpis(
        roi_dependent: dict[int, ROICountTime], loitering_threshold_us: int = 10_000_000
    ) -> dict[str, Any]:
        kpis: dict[str, Any] = {
            "region_occupancy": defaultdict(int),
            "region_utilization": defaultdict(float),
            "region_wise_class_count": defaultdict(lambda: defaultdict(int)),
            "track_id_dwell_time": defaultdict(lambda: defaultdict(float)),
            "avg_dwell_time_per_region": {},
            "unique_tracks_per_region": defaultdict(set),
            "loitering_events": [],
        }

        region_dwell_sum: defaultdict[str, float] = defaultdict(float)
        region_dwell_count: defaultdict[str, int] = defaultdict(int)
        if len(roi_dependent) >= 3:
            pass
        for track_id, obj in roi_dependent.items():
            regions = obj.visited_regions.split(",") if obj.visited_regions else []
            times = obj.region_with_time.split(",") if obj.region_with_time else []
            if len(regions) > 3:
                pass

            for region, time_str in zip(regions, times, strict=False):
                if not region:
                    continue
                try:
                    dwell_us = int(time_str) / 1_000_000
                    kpis["track_id_dwell_time"][track_id][region] += dwell_us
                    kpis["region_occupancy"][region] += 1
                    kpis["region_utilization"][region] += dwell_us
                    kpis["region_wise_class_count"][region][obj.class_name] += 1
                    region_dwell_sum[region] += dwell_us
                    region_dwell_count[region] += 1
                    kpis["unique_tracks_per_region"][region].add(track_id)
                    if dwell_us >= loitering_threshold_us:
                        kpis["loitering_events"].append(
                            {
                                "track_id": track_id,
                                "region": region,
                                "dwell_s": dwell_us / 1_000_000,
                            }
                        )
                except ValueError:
                    continue

        for region in region_dwell_sum:
            kpis["avg_dwell_time_per_region"][region] = (
                region_dwell_sum[region] / region_dwell_count[region]
            ) / 1_000_000

        for r in kpis["unique_tracks_per_region"]:
            kpis["unique_tracks_per_region"][r] = len(kpis["unique_tracks_per_region"][r])

        return kpis

    @staticmethod
    async def count_flat_list(general_ob: list[Any]) -> dict[str, dict[str, dict[str, int]]]:
        summary: dict[str, dict[str, int]] = {}
        for obj in general_ob:
            region_name = obj.first_seen_in_region
            class_name = obj.class_name
            if region_name not in summary:
                summary[region_name] = {}
            if class_name not in summary[region_name]:
                summary[region_name][class_name] = 0
            summary[region_name][class_name] += 1
        return {"region_occupancy": summary}

    @staticmethod
    async def line_counter(kpis_line_tag: dict[int, TagCount]) -> dict[str, Any]:
        regions_wise_track: dict[str, dict[int, bool]] = {}
        regions_wise_count: dict[str, dict[str, int]] = {}
        regions_extra: dict[str, dict[str, int]] = {}

        for track_id, obj in kpis_line_tag.items():
            is_crossed = getattr(obj, "is_line_crossed", False)
            current_region = getattr(obj, "last_seen_in_region", "") or ""

            if current_region not in regions_wise_track:
                regions_wise_track[current_region] = {}
                regions_extra[current_region] = {
                    "in_count": 0,
                    "out_count": 0,
                    "right_count": 0,
                    "left_count": 0,
                }

            regions_wise_track[current_region][track_id] = is_crossed
            regions_extra[current_region]["in_count"] += int(getattr(obj, "in_count", 0) or 0)
            regions_extra[current_region]["out_count"] += int(getattr(obj, "out_count", 0) or 0)
            regions_extra[current_region]["right_count"] += int(getattr(obj, "right_count", 0) or 0)
            regions_extra[current_region]["left_count"] += int(getattr(obj, "left_count", 0) or 0)

        for region, value_dict in regions_wise_track.items():
            true_count = sum(1 for v in value_dict.values() if v is True)
            false_count = sum(1 for v in value_dict.values() if v is False)
            extra = regions_extra.get(
                region, {"in_count": 0, "out_count": 0, "right_count": 0, "left_count": 0}
            )
            regions_wise_count[region] = {
                PASSED: true_count,
                NOT_PASSED: false_count,
                TOTAL: true_count + false_count,
                "in_count": extra["in_count"],
                "out_count": extra["out_count"],
                "right_count": extra["right_count"],
                "left_count": extra["left_count"],
            }

        return {"region_occupancy": regions_wise_count}

    async def update_job_property(self, job_id: str, property_name: str, value: int) -> bool:
        try:
            job_config = self.job_statuses.get(job_id)
            if not job_config:
                return False
            setattr(job_config, property_name, value)
            return True
        except Exception as e:
            logger = container.resolve(ILoggerService)
            await logger.error(
                f"Error updating property '{property_name}' for job {job_id}: {str(e)}"
            )
            return False
