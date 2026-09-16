"""
OmniTrack AI — Detection Router
Start/stop detection, get results — wired to the multi-camera processing pipeline.
Adding a camera and starting detection runs YOLO + ByteTrack on that feed; results come from the pipeline.

Local playback (FYP / no live cameras):
  - Use source="footage:filename.mp4" to run on clips in storage/footage (uploaded or recorded).
  - Or pass a full path to a .mp4/.avi file and stream_type="file".
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Tuple
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import DetectionResult, DetectionFrame
from app.config import settings
from app.ai.segmenter import SAM2Segmenter
from app.ai.pose_mediapipe import MediaPipePose
from app.services.job_persistence import JobPersistence

router = APIRouter(prefix="/api/detection", tags=["Detection"])

FOOTAGE_DIR = Path(settings.FOOTAGE_DIR)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".webm", ".mov"}
_sam2_segmenter: Optional[SAM2Segmenter] = None
_mediapipe_pose: Optional[MediaPipePose] = None


def _resolve_source(source: str, stream_type: str) -> Tuple[str, str]:
    """
    Resolve source and stream_type for pipeline.
    - footage:filename.mp4 → absolute path under FOOTAGE_DIR, stream_type=file
    - Relative path with stream_type=file → under FOOTAGE_DIR if present
    - "0" or numeric string with webcam → keep for OpenCV device index
    """
    source = (source or "").strip()
    stream_type = (stream_type or "rtsp").lower()

    if source.startswith("footage:"):
        name = source.replace("footage:", "", 1).strip().lstrip("/")
        if not name:
            raise HTTPException(status_code=400, detail="footage: requires a filename (e.g. footage:clip.mp4)")
        resolved = FOOTAGE_DIR / name
        if not resolved.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"Footage file not found: {name}. Upload it via Dashboard → Stored CCTV Footage or use /api/footage/upload.",
            )
        return str(resolved.resolve()), "file"

    if stream_type == "file":
        p = Path(source)
        if not p.is_absolute():
            # Try under FOOTAGE_DIR for relative paths
            under_footage = FOOTAGE_DIR / source
            if under_footage.is_file():
                return str(under_footage.resolve()), "file"
        if p.is_file():
            return str(p.resolve()), "file"
        # Let pipeline/OpenCV fail with a clear error if path invalid
        return source, "file"

    if stream_type == "webcam" and (source == "0" or (source.isdigit() and 0 <= int(source) <= 32)):
        return source, "webcam"

    return source, stream_type


def get_pipeline(request: Request):
    """Pipeline is set in main.py lifespan."""
    return request.app.state.pipeline


@router.post("/start/{camera_id}")
async def start_detection(
    camera_id: int,
    request: Request,
    source: str = "0",
    stream_type: str = "webcam",
    zone: str = "default",
    model: str = None,
    tracker: str = "botsort.yaml",
    fps: int = 30,
    skip_frames: int = 1,
    enable_reid: bool = True,
    loop: bool = False,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Start person detection on a camera feed.
    - source: "0" (webcam), path to .mp4, RTSP URL, or "footage:filename.mp4" (stored CCTV)
    - model: Model filename from /api/models (e.g., "yolo11n.pt"). Uses default if not specified.
    - fps: Max capture rate for this feed (applied in the stream reader; clamped 1–240).
    - skip_frames: Process every (skip_frames+1)th captured frame (0 = all captured frames).
    - enable_reid: Enable 512-d Torchreid + global gallery for this feed (CPU/GPU heavy). Disable for lighter multi-cam runs.
    - loop: Replay a file source instead of stopping at its end. Counts and Re-ID
      identities accumulate across laps, so use it for demos, not for real analytics.
    - Use footage: for prototype: run full CV on downloaded store clips as live cameras.
    """
    source, stream_type = _resolve_source(source, stream_type)
    
    # Resolve model path (filename from model_weight, absolute path, or URL)
    model_path = None
    if model:
        if model.startswith(("http://", "https://")):
            # Cache URL downloads under model_weight
            from ultralytics.utils.downloads import attempt_download_asset

            model_name = Path(model).name
            out = Path(settings.MODEL_WEIGHTS_DIR) / model_name
            if not out.exists():
                attempt_download_asset(model, file=str(out))
            model_path = str(out.resolve())
        else:
            candidate = Path(model)
            if candidate.is_file():
                model_path = str(candidate.resolve())
            else:
                model_file = Path(settings.MODEL_WEIGHTS_DIR) / model
                if not model_file.exists():
                    raise HTTPException(status_code=404, detail=f"Model {model} not found in {settings.MODEL_WEIGHTS_DIR}")
                model_path = str(model_file.resolve())

        # Validate model load up-front so pipeline doesn't crash later.
        try:
            from ultralytics import YOLO

            YOLO(model_path)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Failed to load model {model}: {e}")
    
    try:
        pipeline.add_camera(
            camera_id=camera_id,
            source=source,
            stream_type=stream_type,
            zone=zone,
            fps=fps,
            skip_frames=skip_frames,
            model_path=model_path,
            tracker_config=tracker,
            enable_reid=enable_reid,
            loop=loop,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if pipeline.state.value != "running":
        await pipeline.start()
    return {
        "message": f"Detection started on camera {camera_id}",
        "status": "running",
        "source": source,
        "model": model or settings.DEFAULT_YOLO_MODEL,
        "tracker": tracker,
    }


class JobRegionPoint(BaseModel):
    x: float
    y: float


class JobRegion(BaseModel):
    """One drawn region. Shape depends on `type`; extra keys are ignored."""
    type: str
    name: str
    points: Optional[List[JobRegionPoint]] = None
    coordinates: Optional[Dict[str, float]] = None
    line_points: Optional[List[JobRegionPoint]] = None
    object_moving_direction: Optional[str] = None
    tag: Optional[str] = None


class JobConfigRequest(BaseModel):
    """
    Everything the Add Job modal collected that the start call cannot carry as
    query parameters: the drawn regions, the frame they were drawn against, and
    the selected object classes.
    """
    regions: List[JobRegion] = Field(default_factory=list)
    frame_width: int = 0
    frame_height: int = 0
    classes: List[str] = Field(default_factory=list)
    activity_type: Optional[str] = None
    model: Optional[str] = None
    tracker: Optional[str] = None
    source: Optional[str] = None
    zone: Optional[str] = None
    job_id: Optional[str] = None


@router.post("/jobs/{camera_id}")
async def set_job_config(
    camera_id: int,
    body: JobConfigRequest,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Attach a job's regions and class selection to a running camera.

    Regions are stored in the frame coordinates the browser drew them in; the
    pipeline rescales them onto whatever resolution it actually decodes.
    """
    # Cap the number of registered jobs. Each one owns a camera, a detector and
    # a tracker, so this is a real resource limit rather than a UI nicety.
    max_jobs = int(getattr(settings, "MAX_JOBS", 2))
    existing = set(pipeline.get_all_jobs().keys())
    if camera_id not in existing and len(existing) >= max_jobs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job limit reached ({max_jobs}). Delete an existing job before "
                f"creating another."
            ),
        )

    # detections.camera_id is a FK to cameras.id. Without this row every
    # detection insert fails with ForeignKeyViolationError, swallowed at debug
    # level — which is why nothing reached the database before.
    await JobPersistence.ensure_camera(
        camera_id,
        name=f"Camera {camera_id}",
        zone=body.zone or "default",
        source=body.source or "",
    )

    regions = [r.model_dump(exclude_none=False) for r in body.regions]
    pipeline.set_camera_job(
        camera_id=camera_id,
        regions=regions,
        frame_width=body.frame_width,
        frame_height=body.frame_height,
        classes=body.classes,
        meta={
            "activity_type": body.activity_type,
            "model": body.model,
            "tracker": body.tracker,
            "source": body.source,
            "zone": body.zone,
            "job_id": body.job_id or f"JOB-CAM{camera_id:02d}",
        },
    )
    saved = pipeline.get_camera_job(camera_id) or {}
    await JobPersistence.save_job(camera_id, saved)
    return {"status": "ok", "camera_id": camera_id, "regions": len(regions)}


@router.get("/jobs")
async def list_jobs(
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Every configured job, with its live line-crossing counts."""
    jobs = pipeline.get_all_jobs()
    status = pipeline.get_status()
    cam_stats = (status.get("cameras", {}) or {}).get("stats", {}) or {}
    out = []
    for camera_id, job in jobs.items():
        if not job:
            continue
        meta = job.get("meta", {}) or {}
        stats = cam_stats.get(str(camera_id)) or cam_stats.get(camera_id) or {}
        out.append({
            "camera_id": camera_id,
            "job_id": meta.get("job_id") or f"JOB-CAM{camera_id:02d}",
            "activity_type": meta.get("activity_type"),
            "model": meta.get("model"),
            "tracker": meta.get("tracker"),
            "source": meta.get("source"),
            "zone": meta.get("zone"),
            "classes": job.get("classes", []),
            "regions": job.get("regions", []),
            # Back-compat view for the job cards: region -> {in, out, left, right}
            "line_counts": job.get("line_counts", {}),
            # Full KPI snapshot. Shape depends on the activity:
            #   line_passing  -> {by_region, by_class, tracked}
            #   roi_region    -> {by_region, occupancy, tracked}
            #   general_object_detection -> {current, totals, peak, frames_seen}
            "kpi": job.get("kpi", {}),
            "connected": bool(stats.get("connected")),
            "fps": stats.get("fps", 0.0),
            "resolution": stats.get("resolution"),
        })
    out.sort(key=lambda j: j["camera_id"])
    return {"jobs": out, "pipeline_state": status.get("state")}


@router.get("/jobs/{camera_id}/tracks")
async def job_tracks(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Per-track detail behind a job's totals — which track crossed which line, or
    which regions it visited and for how long. Empty for stateless activities
    such as general_object_detection, which returns its recent frames instead.
    """
    kpi = pipeline._camera_kpis.get(camera_id)
    if kpi is None:
        raise HTTPException(status_code=404, detail=f"No job configured for camera {camera_id}")
    if hasattr(kpi, "tracks_as_list"):
        return {"camera_id": camera_id, "tracks": kpi.tracks_as_list()}
    if hasattr(kpi, "recent_as_list"):
        return {"camera_id": camera_id, "frames": kpi.recent_as_list()}
    return {"camera_id": camera_id, "tracks": []}


@router.get("/jobs/history/lines")
async def line_history(
    camera_id: Optional[int] = None,
    job_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Per-line totals from stored history, surviving restarts."""
    return {"regions": await JobPersistence.line_summary(job_id=job_id, camera_id=camera_id)}


@router.get("/jobs/history/roi")
async def roi_history(
    camera_id: Optional[int] = None,
    job_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Per-zone dwell totals from stored history."""
    return {"regions": await JobPersistence.roi_summary(job_id=job_id, camera_id=camera_id)}


@router.get("/jobs/alerts")
async def job_alerts(
    camera_id: Optional[int] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
):
    """Recent threshold breaches (currently ROI dwell overruns)."""
    return {"alerts": await JobPersistence.recent_alerts(camera_id=camera_id, limit=limit)}


@router.get("/jobs/artifacts")
async def job_artifacts(
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Snapshot/clip counts and disk usage for the artifacts folder."""
    return pipeline.artifacts.stats()


@router.delete("/jobs/{camera_id}")
async def delete_job(
    camera_id: int,
    purge_history: bool = False,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Stop the feed and forget its job config.

    History is KEPT by default so past counts remain queryable; pass
    `purge_history=true` to delete the rows as well.
    """
    pipeline.remove_camera(camera_id)
    pipeline.clear_camera_job(camera_id)
    await JobPersistence.end_job(camera_id)
    if purge_history:
        await JobPersistence.purge_job_history(camera_id)
    return {"status": "deleted", "camera_id": camera_id, "history_purged": purge_history}


@router.post("/stop/{camera_id}")
async def stop_detection(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Stop detection on a camera feed (removes from pipeline)."""
    pipeline.remove_camera(camera_id)
    pipeline.clear_camera_job(camera_id)
    return {"message": f"Detection stopped on camera {camera_id}", "status": "stopped"}


@router.get("/status")
async def detection_status(
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Get detection status from the pipeline (active cameras, state)."""
    status = pipeline.get_status()
    cameras = status.get("cameras", {})
    stats = cameras.get("stats", {})
    frame_counts = status.get("processing", {}).get("frame_counts", {})
    cam_ids = list(stats.keys()) or list(frame_counts.keys())
    return {
        "state": status.get("state", "idle"),
        "active_cameras": cam_ids,
        "total_active": cameras.get("active", 0) or len(cam_ids),
        "total_cameras": cameras.get("total", 0),
        "camera_stats": stats,
    }


@router.post("/recording/start/{camera_id}")
async def start_recording(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Start recording this camera's feed (with detection overlay) to stored footage."""
    storage_guard = getattr(pipeline, "storage_guard", None)
    if storage_guard is not None:
        check = storage_guard.check_can_record()
        if not check.get("ok"):
            raise HTTPException(
                status_code=507,
                detail=(
                    f"Insufficient disk space for recording "
                    f"(free={check['free_mb']:.1f}MB, required={check['min_required_mb']:.1f}MB)"
                ),
            )
    result = pipeline.start_recording(camera_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/recording/stop/{camera_id}")
async def stop_recording(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Stop recording and save the clip to footage storage (Dashboard → Stored CCTV Footage)."""
    return pipeline.stop_recording(camera_id)


@router.get("/recording/status")
async def recording_status(
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Get which cameras are currently recording."""
    return pipeline.get_recording_status()


@router.get("/results/{camera_id}", response_model=List[DetectionResult])
async def get_detection_results(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Get latest detection results for a camera from the pipeline."""
    raw = pipeline.get_latest_results(camera_id)
    if not raw:
        return []
    # getattr's default is evaluated EAGERLY, so the old one-liner called
    # raw.get() even when raw was a CameraResult dataclass — making this
    # endpoint a guaranteed 500 whenever the pipeline had results.
    if not raw:
        detections = []
    elif isinstance(raw, dict):
        detections = raw.get("detections", [])
    else:
        detections = getattr(raw, "detections", []) or []
    out: List[DetectionResult] = []
    for d in (detections or [])[:50]:
        if isinstance(d, dict):
            bbox = d.get("bbox") or d.get("box", [])
            if len(bbox) < 4:
                continue
            out.append(
                DetectionResult(
                    camera_id=camera_id,
                    track_id=d.get("track_id"),
                    bbox=[float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                    confidence=float(d.get("confidence", 0)),
                    class_name=str(d.get("class_name", "person")),
                    zone=d.get("zone"),
                )
            )
    return out


@router.post("/segment/run")
async def segment_once(
    camera_id: int,
    bbox: Optional[List[float]] = None,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Run SAM2 segmentation on the latest frame for a camera (on-demand only).
    """
    if not getattr(settings, "ENABLE_SAM2", False):
        raise HTTPException(status_code=400, detail="SAM2 is disabled in current profile")
    frame_tuple = await pipeline.stream_manager.get_frame_async(camera_id, timeout=0.2)
    if not frame_tuple:
        raise HTTPException(status_code=404, detail=f"No live frame available for camera {camera_id}")
    frame, _ts = frame_tuple
    global _sam2_segmenter
    if _sam2_segmenter is None:
        _sam2_segmenter = SAM2Segmenter(weights=getattr(settings, "SAM2_WEIGHTS", "sam2_b.pt"))
    result = await asyncio.to_thread(_sam2_segmenter.segment, frame, bbox, None)
    return {
        "camera_id": camera_id,
        "ok": result.get("ok", False),
        "masks": result.get("masks", []),
        "reason": result.get("reason"),
    }


@router.post("/pose/run")
async def pose_once(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Run MediaPipe pose landmarks on the latest frame for a camera (on-demand).
    """
    if not getattr(settings, "ENABLE_MEDIAPIPE", True):
        raise HTTPException(status_code=400, detail="MediaPipe is disabled")
    frame_tuple = await pipeline.stream_manager.get_frame_async(camera_id, timeout=0.2)
    if not frame_tuple:
        raise HTTPException(status_code=404, detail=f"No live frame available for camera {camera_id}")
    frame, _ts = frame_tuple
    global _mediapipe_pose
    if _mediapipe_pose is None:
        _mediapipe_pose = MediaPipePose()
    result = await asyncio.to_thread(_mediapipe_pose.run, frame)
    return {"camera_id": camera_id, **result}
