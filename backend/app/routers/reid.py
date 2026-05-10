"""
OmniTrack AI — Re-ID Router
Person search, journey tracking, cross-camera identity matching.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from datetime import datetime, timezone

from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import ReIDMatch, CustomerJourneyResponse
from app.database import get_db
from app.services.crud import AnalyticsService

router = APIRouter(prefix="/api/reid", tags=["Re-Identification"])


@router.post("/search", response_model=List[ReIDMatch])
async def search_person(
    top_k: int = 10,
    threshold: float = 0.6,
    current_user: User = Depends(get_current_user),
):
    """
    Reserved: gallery search from uploaded embedding/image.
    Use live pipeline + /reid/active for production tracking.
    """
    raise HTTPException(
        status_code=501,
        detail="Use the live pipeline gallery via /reid/active and Cross-feed WebSocket events.",
    )


@router.get("/journey/{global_id}", response_model=CustomerJourneyResponse)
async def get_person_journey(
    global_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persisted journey legs from PostgreSQL (filled while the pipeline runs)."""
    legs = await AnalyticsService.get_journey(db, global_id)
    if not legs:
        raise HTTPException(
            status_code=404,
            detail=f"No journey data for {global_id}. Ensure Re-ID is enabled and the pipeline has run.",
        )
    journey_data: List[Dict[str, Any]] = []
    for leg in legs:
        journey_data.append({
            "camera_id": leg.camera_id,
            "zone": leg.zone or "",
            "timestamp": leg.entry_time.isoformat() if leg.entry_time else None,
            "duration": float(leg.dwell_time or 0),
            "dwell_time": float(leg.dwell_time or 0),
        })
    entry_time = legs[0].entry_time
    exit_time = legs[-1].exit_time or legs[-1].entry_time
    total_duration = sum(float(l.dwell_time or 0) for l in legs)
    zones_visited = len({(l.zone or "") for l in legs if l.zone})
    return CustomerJourneyResponse(
        global_id=global_id,
        entry_time=entry_time,
        exit_time=exit_time,
        total_duration=total_duration,
        zones_visited=max(zones_visited, 1),
        journey_data=journey_data,
    )


@router.get("/active", response_model=List[ReIDMatch])
async def get_active_persons(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Currently active global identities from the live pipeline (Re-ID-enabled feeds only)."""
    pipeline = request.app.state.pipeline
    tracks = getattr(pipeline.global_state, "active_tracks", None) or {}
    now = datetime.now(timezone.utc)
    out: List[ReIDMatch] = []
    for gid, info in tracks.items():
        if not gid or not isinstance(info, dict):
            continue
        if not str(gid).startswith("PERSON-"):
            continue
        raw_ts = info.get("last_seen")
        try:
            if isinstance(raw_ts, (int, float)):
                ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
            else:
                ts = now
        except (ValueError, OSError):
            ts = now
        out.append(
            ReIDMatch(
                global_id=str(gid),
                camera_id=int(info.get("camera_id", 0)),
                confidence=1.0,
                timestamp=ts,
                bbox=info.get("bbox"),
            )
        )
    out.sort(key=lambda r: r.timestamp, reverse=True)
    return out[:64]
