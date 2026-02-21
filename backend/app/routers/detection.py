"""
OmniTrack AI — Detection Router
Start/stop detection, get results — wired to the multi-camera processing pipeline.
Adding a camera and starting detection runs YOLO + ByteTrack on that feed; results come from the pipeline.
For prototype: use source="footage:filename.mp4" to run CV on stored/downloaded CCTV as a live camera.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Dict, Any, Optional
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import DetectionResult, DetectionFrame
from app.config import settings

router = APIRouter(prefix="/api/detection", tags=["Detection"])


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
    current_user: User = Depends(get_current_user),
    pipeline=Depends(get_pipeline),
):
    """
    Start person detection on a camera feed.
    - source: "0" (webcam), path to .mp4, RTSP URL, or "footage:filename.mp4" (stored CCTV)
    - Use footage: for prototype: run full CV on downloaded store clips as live cameras.
    """
    source, stream_type = _resolve_source(source, stream_type)
    pipeline.add_camera(
        camera_id=camera_id,
        source=source,
        stream_type=stream_type,
        zone=zone,
    )
    if pipeline.state.value != "running":
        await pipeline.start()
    return {"message": f"Detection started on camera {camera_id}", "status": "running", "source": source}


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
