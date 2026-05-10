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
from typing import List, Dict, Any, Optional, Tuple
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import DetectionResult, DetectionFrame
from app.config import settings
from app.ai.segmenter import SAM2Segmenter
from app.ai.pose_mediapipe import MediaPipePose

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


@router.post("/stop/{camera_id}")
async def stop_detection(
    camera_id: int,
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """Stop detection on a camera feed (removes from pipeline)."""
    pipeline.remove_camera(camera_id)
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
    detections = getattr(raw, "detections", raw.get("detections", [])) if raw else []
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
