"""
OmniTrack AI — Re-ID Router
Person search, journey tracking, cross-camera identity matching.
"""

from fastapi import APIRouter, Depends, Request, HTTPException, Response
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import ReIDMatch, ReIDJourney, CustomerJourneyResponse

router = APIRouter(prefix="/api/reid", tags=["Re-Identification"])


def _get_pipeline(request: Request):
    return request.app.state.pipeline


@router.get("/status")
async def reid_status(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return whether Re-ID is currently enabled and gallery size."""
    pipeline = _get_pipeline(request)
    return {
        "enabled": pipeline.is_reid_enabled(),
        "gallery_size": len(pipeline.reid._gallery) if pipeline and pipeline.reid else 0,
        "embedding_dim": 512,
        "model": pipeline.reid.model_name if pipeline and pipeline.reid else None,
    }


@router.post("/toggle")
async def reid_toggle(
    request: Request,
    enabled: bool,
    current_user: User = Depends(get_current_user),
):
    """Enable/disable Re-ID phase at runtime. When disabled, no global_id is assigned/drawn."""
    pipeline = _get_pipeline(request)
    new_state = pipeline.set_reid_enabled(enabled)
    return {"enabled": new_state}


@router.get("/active")
async def get_active_persons(
    request: Request,
    window_seconds: float = 15.0,
    current_user: User = Depends(get_current_user),
):
    """
    Return global_ids currently active (seen within `window_seconds`).
    Backed by the live pipeline — NOT mock data.
    """
    pipeline = _get_pipeline(request)
    return pipeline.get_active_persons(active_window_s=window_seconds)


@router.get("/journey/{global_id}")
async def get_person_journey(
    global_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Get a person's live journey across cameras from the pipeline.
    Each leg = one continuous presence on a single camera.
    """
    pipeline = _get_pipeline(request)
    journey = pipeline.get_person_journey(global_id)
    if not journey:
        # Return empty journey shape rather than 404 so the UI handles gracefully
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "global_id": global_id,
            "entry_time": now_iso,
            "exit_time": now_iso,
            "total_duration": 0.0,
            "zones_visited": 0,
            "cameras_visited": 0,
            "journey_data": [],
        }
    return journey


@router.get("/matches/recent")
async def recent_cross_matches(
    request: Request,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
):
    """
    Return recent cross-camera handoff events (the same person seen
    moving from one camera to another).
    """
    pipeline = _get_pipeline(request)
    return pipeline.get_recent_cross_matches(limit=limit)


@router.get("/snapshot/{global_id}/{camera_id}")
async def get_person_snapshot(
    global_id: str,
    camera_id: int,
    request: Request,
):
    """
    Return the JPEG thumbnail of a person on a specific camera.
    Used by the UI to display matched-person images per camera.
    NOTE: no auth so the <img> tag works directly without injecting tokens.
    """
    pipeline = _get_pipeline(request)
    jpeg_bytes = pipeline.get_person_snapshot(global_id, camera_id)
    if jpeg_bytes is None:
        raise HTTPException(status_code=404, detail="No snapshot for this person on this camera")
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "max-age=2"},  # short cache so refresh shows newest crop
    )


@router.get("/snapshots/{global_id}")
async def list_person_snapshots(
    global_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """List camera_ids for which we have snapshots of a given person."""
    pipeline = _get_pipeline(request)
    cams = pipeline.list_person_snapshots(global_id)
    return {
        "global_id": global_id,
        "camera_ids": cams,
        "snapshot_urls": [f"/api/reid/snapshot/{global_id}/{cid}" for cid in cams],
    }


@router.post("/search", response_model=List[ReIDMatch])
async def search_person(
    top_k: int = 10,
    threshold: float = 0.6,
    current_user: User = Depends(get_current_user),
):
    """Search for a person by embedding similarity (legacy; returns mock)."""
    now = datetime.now(timezone.utc)
    return [
        ReIDMatch(
            global_id=f"PERSON-{i:04d}",
            camera_id=(i % 4) + 1,
            confidence=0.92 - i * 0.05,
            timestamp=now - timedelta(minutes=i * 5),
            bbox=[100 + i * 30, 200, 80, 180],
        )
        for i in range(min(top_k, 5))
    ]
