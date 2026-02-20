"""
OmniTrack AI — Detection Router
Start/stop detection, get results, live feed processing.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import DetectionResult, DetectionFrame

router = APIRouter(prefix="/api/detection", tags=["Detection"])


# In-memory state (replaced by proper service in production)
_detection_state: Dict[str, Any] = {
    "active_cameras": {},
    "results": [],
}


@router.post("/start/{camera_id}")
async def start_detection(
    camera_id: int,
    confidence: float = 0.5,
    current_user: User = Depends(get_current_user),
):
    """Start person detection on a camera feed."""
    _detection_state["active_cameras"][camera_id] = {
        "status": "running",
        "confidence": confidence,
    }
    return {"message": f"Detection started on camera {camera_id}", "status": "running"}


@router.post("/stop/{camera_id}")
async def stop_detection(
    camera_id: int,
    current_user: User = Depends(get_current_user),
):
    """Stop detection on a camera feed."""
    if camera_id in _detection_state["active_cameras"]:
        del _detection_state["active_cameras"][camera_id]
    return {"message": f"Detection stopped on camera {camera_id}", "status": "stopped"}


@router.get("/status")
async def detection_status(current_user: User = Depends(get_current_user)):
    """Get detection status across all cameras."""
    return {
        "active_cameras": list(_detection_state["active_cameras"].keys()),
        "total_active": len(_detection_state["active_cameras"]),
    }


@router.get("/results/{camera_id}", response_model=List[DetectionResult])
async def get_detection_results(
    camera_id: int,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
):
    """Get recent detection results for a camera."""
    # Mock results for demo
    return [
        DetectionResult(
            camera_id=camera_id, track_id=i,
            bbox=[100.0 + i * 50, 200.0, 80.0, 180.0],
            confidence=0.85 + (i % 5) * 0.02,
            class_name="person",
            zone="entrance",
        )
        for i in range(min(limit, 5))
    ]
