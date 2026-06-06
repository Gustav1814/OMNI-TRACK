"""
OmniTrack AI — Detection Router
Start/stop detection, get results — wired to the multi-camera processing pipeline.
Adding a camera and starting detection runs YOLO + ByteTrack on that feed; results come from the pipeline.

Local playback (FYP / no live cameras):
  - Use source="footage:filename.mp4" to run on clips in storage/footage (uploaded or recorded).
  - Or pass a full path to a .mp4/.avi file and stream_type="file".
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Dict, Any, Optional, Tuple
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import DetectionResult, DetectionFrame
from app.config import settings
from app.services import model_selection as model_select

router = APIRouter(prefix="/api/detection", tags=["Detection"])

FOOTAGE_DIR = Path(settings.FOOTAGE_DIR)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".webm", ".mov"}
MODEL_EXTENSIONS = {".pt", ".onnx", ".engine", ".tflite"}


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


def _parse_model_selection(model: Optional[str] = None, models: Optional[str] = None) -> List[str]:
    raw = models if models is not None else model
    if not raw:
        return []
    selected = []
    for item in str(raw).replace(";", ",").split(","):
        name = os.path.basename(item.strip())
        if name and name not in selected:
            selected.append(name)
    return selected


def _resolve_model_paths(selected: List[str]) -> List[str]:
    if not selected:
        return []
    paths = []
    for name in selected:
        model_file = Path(settings.MODEL_WEIGHTS_DIR) / name
        if model_file.exists():
            paths.append(str(model_file.resolve()))
            continue
        cwd_candidate = Path(name)
        if cwd_candidate.is_file():
            paths.append(str(cwd_candidate.resolve()))
            continue
        if name == settings.DEFAULT_YOLO_MODEL:
            # Ultralytics can resolve/download the default weight by filename.
            paths.append(name)
            continue
        raise HTTPException(status_code=404, detail=f"Model {name} not found in {settings.MODEL_WEIGHTS_DIR}")
    return paths


def _default_weight_names() -> List[str]:
    return [settings.DEFAULT_YOLO_MODEL]


def _list_all_weight_names() -> List[str]:
    weights_dir = Path(settings.MODEL_WEIGHTS_DIR)
    if not weights_dir.is_dir():
        return []
    names = [
        f.name
        for f in weights_dir.iterdir()
        if f.is_file() and f.suffix.lower() in MODEL_EXTENSIONS and not f.name.startswith(".")
    ]
    return sorted(dict.fromkeys(names))


def _select_smart_weight_names(zone: str = "", source: str = "", prefer_ensemble: bool = False) -> List[str]:
    """
    Select a small compatible model set for a feed.
    Brute-force all weights is slow and noisy because face, fire, product, and
    general-person weights solve different tasks.
    """
    names = _list_all_weight_names()
    if not names:
        return []

    lower_by_name = {name: name.lower() for name in names}

    def first_matching(*needles: str) -> Optional[str]:
        for needle in needles:
            for name, lower in lower_by_name.items():
                if needle in lower:
                    return name
        return None

    def lightweight_general_candidates() -> List[str]:
        ordered = []
        for needle in ("yolo11", "yolov8", "yolo26", "general"):
            hit = first_matching(needle)
            if hit and hit not in ordered:
                ordered.append(hit)
        for name in names:
            lower = lower_by_name[name]
            if name not in ordered and not any(k in lower for k in ("face", "fire", "smoke", "product", "pose", "pe_")):
                ordered.append(name)
        return ordered

    zone_text = f"{zone} {source}".lower()
    selected: List[str] = []
    general = lightweight_general_candidates()

    def add(name: Optional[str]):
        if name and name not in selected:
            selected.append(name)

    if any(k in zone_text for k in ("fire", "smoke", "kitchen", "storage", "safety")):
        add(first_matching("fire", "smoke"))
        add(general[0] if general else None)
    elif any(k in zone_text for k in ("shelf", "product", "aisle", "inventory", "stock")):
        add(first_matching("product"))
        add(general[0] if general else None)
    else:
        add(general[0] if general else None)
        if prefer_ensemble and len(general) > 1:
            add(general[1])

    # Auto should stay fast. Manual ensemble remains available for broader experiments.
    return selected[:2] or names[:1]


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
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Start person detection on a camera feed.
    - source: "0" (webcam), path to .mp4, RTSP URL, or "footage:filename.mp4" (stored CCTV)
    - model: Model filename from /api/models (e.g., "yolo11n.pt"). Uses default if not specified.
    - Use footage: for prototype: run full CV on downloaded store clips as live cameras.
    """
    source, stream_type = _resolve_source(source, stream_type)
    
    mode = (model_mode or "manual").strip().lower()
    try:
        selected_models, effective_model_mode = model_select.select_model_names(
            model_mode=mode,
            model=model,
            models=models,
            zone=zone,
            source=source,
        )
        model_paths = model_select.resolve_model_paths(selected_models)
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
        "source": source,
        "model": selected_models[0] if len(selected_models) == 1 else None,
        "models": selected_models or [settings.DEFAULT_YOLO_MODEL],
        "model_mode": effective_model_mode,
        "requested_model_mode": mode,
        "reid_enabled": enable_reid,
        "tracker": tracker,
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
                    model_path=d.get("model_path"),
                    ensemble_models=d.get("ensemble_models"),
                )
            )
    return out
