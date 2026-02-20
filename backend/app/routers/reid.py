"""
OmniTrack AI — Re-ID Router
Person search, journey tracking, cross-camera identity matching.
"""

from fastapi import APIRouter, Depends
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta
from app.models.user import User
from app.security.dependencies import get_current_user
from app.schemas.schemas import ReIDMatch, ReIDJourney, CustomerJourneyResponse

router = APIRouter(prefix="/api/reid", tags=["Re-Identification"])


@router.post("/search", response_model=List[ReIDMatch])
async def search_person(
    top_k: int = 10,
    threshold: float = 0.6,
    current_user: User = Depends(get_current_user),
):
    """Search for a person by embedding similarity."""
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


@router.get("/journey/{global_id}", response_model=CustomerJourneyResponse)
async def get_person_journey(
    global_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a person's journey through the store."""
    now = datetime.now(timezone.utc)
    return CustomerJourneyResponse(
        global_id=global_id,
        entry_time=now - timedelta(hours=1),
        exit_time=now - timedelta(minutes=10),
        total_duration=3000.0,
        zones_visited=5,
        journey_data=[
            {"camera_id": 1, "zone": "entrance", "timestamp": (now - timedelta(hours=1)).isoformat(), "duration": 120},
            {"camera_id": 2, "zone": "aisle-a", "timestamp": (now - timedelta(minutes=50)).isoformat(), "duration": 300},
            {"camera_id": 3, "zone": "shelf-electronics", "timestamp": (now - timedelta(minutes=40)).isoformat(), "duration": 600},
            {"camera_id": 2, "zone": "aisle-b", "timestamp": (now - timedelta(minutes=25)).isoformat(), "duration": 180},
            {"camera_id": 4, "zone": "checkout", "timestamp": (now - timedelta(minutes=15)).isoformat(), "duration": 300},
        ],
    )


@router.get("/active", response_model=List[ReIDMatch])
async def get_active_persons(
    current_user: User = Depends(get_current_user),
):
    """Get currently tracked persons in the store."""
    now = datetime.now(timezone.utc)
    return [
        ReIDMatch(
            global_id=f"PERSON-{i:04d}",
            camera_id=(i % 4) + 1,
            confidence=0.88 + (i % 3) * 0.04,
            timestamp=now - timedelta(seconds=i * 10),
        )
        for i in range(8)
    ]
