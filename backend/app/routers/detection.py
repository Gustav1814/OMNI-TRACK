"""
OmniTrack AI — Detection Router
Start/stop detection, get results — wired to the multi-camera processing pipeline.
Adding a camera and starting detection runs YOLO + ByteTrack on that feed; results come from the pipeline.

Local playback (FYP / no live cameras):
  - Use source="footage:filename.mp4" to run on clips in storage/footage (uploaded or recorded).
  - Or pass a full path to a .mp4/.avi file and stream_type="file".
"""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Tuple
from app.models.user import User
from app.security.dependencies import get_current_user
from app.security.input_safety import UnsafeInputError, redact_source, resolve_video_file, validate_camera_source
from app.schemas.schemas import DetectionResult
from app.config import settings
from app.services import model_selection as model_select

router = APIRouter(prefix="/api/detection", tags=["Detection"])

FOOTAGE_DIR = Path(settings.FOOTAGE_DIR)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".webm", ".mov"}


def _resolve_source(source: str, stream_type: str) -> Tuple[str, str]:
    """
    Validate and resolve a source for the pipeline (SSRF / path-traversal safe).
    The stream type follows from the validated source; ``stream_type`` is kept for API compatibility.
    - footage:filename.mp4 -> real path of a clip inside FOOTAGE_DIR, stream_type=file
    - files (absolute or relative to a VIDEO_SOURCE_DIRS root) -> real path, stream_type=file
    - "0".."99" -> webcam index, only when ALLOW_WEBCAM_SOURCES=true
    - rtsp(s):// any non-link-local host; http(s):// only CAMERA_SOURCE_ALLOWLIST hosts
    Anything else -> 422.
    """
    source = (source or "").strip()
    try:
        if source.startswith("footage:"):
            name = source.replace("footage:", "", 1).strip().lstrip("/")
            if not name:
                raise HTTPException(status_code=400, detail="footage: requires a filename (e.g. footage:clip.mp4)")
            try:
                return str(resolve_video_file(name, [FOOTAGE_DIR])), "file"
            except UnsafeInputError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="Footage file not found. Upload it via Dashboard -> Stored CCTV Footage or /api/footage/upload.",
                ) from exc
        safe = validate_camera_source(source)
    except UnsafeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scheme = urlsplit(safe).scheme.lower()
    if scheme in ("rtsp", "rtsps"):
        return safe, "rtsp"
    if scheme in ("http", "https"):
        return safe, "http"
    if safe.isdigit():
        return safe, "webcam"
    return safe, "file"


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
    models: str = None,
    model_mode: str = "manual",
    fps: int = settings.PROCESSING_FPS,
    skip_frames: int = settings.DEFAULT_SKIP_FRAMES,
    enable_reid: bool = True,
    tracker: str = "bytetrack",
    loop: bool = False,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Start person detection on a camera feed.
    - source: "0" (webcam), path to .mp4, RTSP URL, or "footage:filename.mp4" (stored CCTV)
    - model/models/model_mode: accepted for compatibility. Every camera uses the one person
      detector chosen from evaluation (GET /api/models/current); ensemble modes return 400.
    - Use footage: for prototype: run full CV on downloaded store clips as live cameras.
    - loop: video files only. False (default) stops the camera at end of file (status shows
      finished=true); True replays the file as a new timeline each pass.
    """
    source, stream_type = _resolve_source(source, stream_type)
    
    mode = (model_mode or "manual").strip().lower()
    try:
        # Phase 0: ensemble / auto_all modes are disabled -> 400.
        model_select.ensure_model_mode_allowed(model_mode=mode, model=model, models=models)
        selected_models, effective_model_mode = model_select.select_model_names(
            model_mode=mode,
            model=model,
            models=models,
            zone=zone,
            source=source,
        )
        model_paths = model_select.resolve_model_paths(selected_models)
    except UnsafeInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    try:
        await request.app.state.cache.invalidate_pipeline_state()
        pipeline.add_camera(
            camera_id=camera_id,
            source=source,
            stream_type=stream_type,
            zone=zone,
            fps=fps,
            skip_frames=skip_frames,
            model_path=model_paths or None,
            enable_reid=enable_reid,
            tracker_mode=tracker,
            loop=loop,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pipeline.state.value != "running":
        await pipeline.start()
    status = pipeline.get_status()
    await request.app.state.cache.cache_pipeline_state(status)
    return {
        "message": f"Detection started on camera {camera_id}",
        "status": "running",
        "source": redact_source(source),
        "model": pipeline.detector.name if pipeline.detector else model_select.person_model_name(),
        "models": [pipeline.detector.name if pipeline.detector else model_select.person_model_name()],
        "model_mode": effective_model_mode,
        "requested_model_mode": mode,
        "reid_enabled": enable_reid,
        "tracker": tracker,
        "loop": loop,
        "pipeline_state": status,
    }


@router.post("/stop/{camera_id}")
async def stop_detection(
    camera_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Stop detection on a camera feed (removes from pipeline)."""
    await request.app.state.cache.invalidate_pipeline_state()
    pipeline.remove_camera(camera_id)
    status = pipeline.get_status()
    await request.app.state.cache.cache_pipeline_state(status)
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
    if raw is None:
        return []
    out: List[DetectionResult] = []
    for d in (raw.detections or [])[:50]:
        bbox = d.get("bbox") or []
        if len(bbox) < 4:
            continue
        out.append(
            DetectionResult(
                camera_id=camera_id,
                track_id=d.get("track_id"),
                bbox=[float(v) for v in bbox[:4]],
                confidence=float(d.get("confidence", 0.0)),
                class_name=str(d.get("class_name", "person")),
                zone=d.get("zone"),
                model_path=pipeline.detector.name if pipeline.detector else None,
            )
        )
    return out
