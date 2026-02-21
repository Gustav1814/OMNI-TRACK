"""
OmniTrack AI — Analytics Routers
Combined router for: synopsis, shelf, fire, crowd, checkout, emotion, audit, vibe.
Audit endpoints use real DB and SHA-256 chain verification (proposal: Decoupled Security Pipeline).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
import random
from app.database import get_db
from app.models.user import User
from app.security.dependencies import get_current_user
from app.services.crud import AuditService
from app.schemas.schemas import (
    SynopsisResponse, ShelfEngagement, FireAlert, CrowdStatus,
    CheckoutMetrics, EmotionResult, EmotionZoneAggregation,
    AuditEntry, AuditChainStatus, StoreVibe, FootTrafficData,
    DemographicData, PeakHourData, PeakHoursSummary, DashboardOverview,
    CustomerJourneyResponse,
)

# --- Synopsis Router ---
synopsis_router = APIRouter(prefix="/api/synopsis", tags=["Video Synopsis"])


@synopsis_router.get("/", response_model=List[SynopsisResponse])
async def list_synopses(current_user: User = Depends(get_current_user)):
    return [
        SynopsisResponse(
            id=1, camera_id=1, original_duration=3600, synopsis_duration=360,
            compression_ratio=10.0, output_path="/data/synopsis/cam1_synopsis.mp4", status="completed"
        ),
        SynopsisResponse(
            id=2, camera_id=2, original_duration=7200, synopsis_duration=480,
            compression_ratio=15.0, output_path="/data/synopsis/cam2_synopsis.mp4", status="completed"
        ),
    ]


@synopsis_router.post("/generate")
async def generate_synopsis(
    camera_id: int, hours: int = 1,
    current_user: User = Depends(get_current_user),
):
    return {"message": f"Synopsis generation queued for camera {camera_id}", "estimated_time": f"{hours * 2} minutes"}


# --- Shelf Router ---
shelf_router = APIRouter(prefix="/api/shelf", tags=["Shelf Analytics"])


@shelf_router.get("/engagement", response_model=List[ShelfEngagement])
async def get_shelf_engagement(current_user: User = Depends(get_current_user)):
    zones = ["Electronics", "Groceries", "Clothing", "Home & Garden", "Sports", "Beauty", "Toys", "Books"]
    return [
        ShelfEngagement(
            zone_id=f"zone-{i}", zone_name=z,
            avg_dwell_time=round(random.uniform(15, 180), 1),
            visit_count=random.randint(20, 300),
            engagement_score=round(random.uniform(20, 95), 1),
            rank=i + 1,
        )
        for i, z in enumerate(zones)
    ]


@shelf_router.get("/top-zones")
async def get_top_zones(n: int = 5, current_user: User = Depends(get_current_user)):
    zones = ["Electronics", "Beauty", "Groceries", "Sports", "Home & Garden"]
    return [{"zone_name": z, "engagement_score": round(95 - i * 8, 1), "rank": i + 1} for i, z in enumerate(zones[:n])]


# --- Fire Router ---
fire_router = APIRouter(prefix="/api/fire", tags=["Fire & Smoke Detection"])


@fire_router.get("/alerts", response_model=List[Dict[str, Any]])
async def get_fire_alerts(limit: int = 20, current_user: User = Depends(get_current_user)):
    return [
        {
            "id": i,
            "alert_type": random.choice(["fire", "smoke"]),
            "confidence": round(random.uniform(0.75, 0.99), 3),
            "camera_id": random.randint(1, 6),
            "zone": random.choice(["warehouse", "kitchen", "loading-dock", "storage"]),
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 48))).isoformat(),
            "status": random.choice(["active", "resolved", "false_alarm"]),
        }
        for i in range(min(limit, 5))
    ]


@fire_router.get("/status")
async def fire_status(current_user: User = Depends(get_current_user)):
    return {"active_alerts": 0, "total_today": 2, "system_status": "monitoring", "cameras_covered": 6}


# --- Crowd Router ---
crowd_router = APIRouter(prefix="/api/crowd", tags=["Crowd Density"])


@crowd_router.get("/status", response_model=List[CrowdStatus])
async def get_crowd_status(current_user: User = Depends(get_current_user)):
    zones = [
        ("Entrance", 12, "medium"), ("Main Floor", 45, "high"),
        ("Food Court", 8, "low"), ("Electronics", 22, "medium"),
        ("Checkout Area", 35, "high"), ("Parking", 5, "low"),
    ]
    return [
        CrowdStatus(
            zone=name, person_count=count, density=round(count / 50, 3),
            classification=cls, threshold=50.0, camera_id=i + 1,
        )
        for i, (name, count, cls) in enumerate(zones)
    ]


@crowd_router.get("/history/{zone}")
async def crowd_history(zone: str, limit: int = 24, current_user: User = Depends(get_current_user)):
    return [
        {"hour": h, "count": random.randint(5, 50), "classification": random.choice(["low", "medium", "high"])}
        for h in range(limit)
    ]


# --- Checkout Router ---
checkout_router = APIRouter(prefix="/api/checkout", tags=["Checkout Analytics"])


@checkout_router.get("/metrics", response_model=List[CheckoutMetrics])
async def get_checkout_metrics(current_user: User = Depends(get_current_user)):
    return [
        CheckoutMetrics(
            lane_id=f"lane-{i}", queue_length=random.randint(0, 8),
            avg_service_time=round(random.uniform(60, 300), 1),
            throughput=round(random.uniform(15, 45), 1),
            current_wait_estimate=round(random.uniform(0, 600), 0),
            camera_id=i + 1,
        )
        for i in range(1, 7)
    ]


@checkout_router.get("/summary")
async def checkout_summary(current_user: User = Depends(get_current_user)):
    return {
        "total_lanes": 6, "active_lanes": 5,
        "total_served_today": random.randint(200, 500),
        "avg_service_time": round(random.uniform(120, 240), 1),
        "avg_wait_time": round(random.uniform(60, 180), 1),
        "busiest_lane": "lane-3",
    }


# --- Emotion Router ---
emotion_router = APIRouter(prefix="/api/emotion", tags=["Emotion Recognition"])


@emotion_router.get("/current", response_model=List[EmotionZoneAggregation])
async def get_current_emotions(current_user: User = Depends(get_current_user)):
    zones = ["Entrance", "Main Floor", "Electronics", "Food Court", "Checkout"]
    emotions = ["happy", "neutral", "sad", "surprise", "angry"]
    return [
        EmotionZoneAggregation(
            zone=z, dominant_emotion=random.choice(emotions),
            emotion_distribution={e: round(random.uniform(0, 0.4), 3) for e in emotions},
            sample_count=random.randint(20, 100),
            sentiment_score=round(random.uniform(-0.3, 0.8), 3),
        )
        for z in zones
    ]


@emotion_router.get("/store-sentiment")
async def store_sentiment(current_user: User = Depends(get_current_user)):
    return {
        "overall_sentiment": round(random.uniform(0.2, 0.7), 3),
        "dominant_emotion": "happy",
        "total_faces_analyzed": random.randint(100, 500),
    }


# --- Audit Router ---
audit_router = APIRouter(prefix="/api/audit", tags=["Security Audit"])


@audit_router.get("/logs", response_model=List[AuditEntry])
async def get_audit_logs(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return tamper-evident audit log from DB (SHA-256 chain)."""
    logs = await AuditService.get_logs(db, limit=limit)
    return [
        AuditEntry(
            id=e.id,
            event_type=e.event_type,
            user_id=e.user_id,
            description=e.description,
            current_hash=e.current_hash,
            previous_hash=e.previous_hash,
            timestamp=e.timestamp,
        )
        for e in logs
    ]


@audit_router.get("/verify", response_model=AuditChainStatus)
async def verify_chain(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify integrity of the entire audit chain (100% tamper detection per proposal)."""
    result = await AuditService.verify_integrity(db)
    return AuditChainStatus(
        valid=result["valid"],
        broken_at=result.get("broken_at"),
        total=result["total"],
    )


# --- Store Vibe Router ---
vibe_router = APIRouter(prefix="/api/vibe", tags=["Store Vibe"])


@vibe_router.get("/current", response_model=StoreVibe)
async def get_current_vibe(current_user: User = Depends(get_current_user)):
    score = round(random.uniform(45, 85), 1)
    labels = {(0, 20): "Quiet", (20, 40): "Calm", (40, 60): "Steady", (60, 80): "Energetic", (80, 101): "Buzzing"}
    label = next(l for (lo, hi), l in labels.items() if lo <= score < hi)
    return StoreVibe(
        overall_score=score,
        sentiment_score=round(random.uniform(40, 80), 1),
        energy_score=round(random.uniform(30, 90), 1),
        engagement_score=round(random.uniform(35, 85), 1),
        foot_traffic_score=round(random.uniform(25, 75), 1),
        timestamp=datetime.now(timezone.utc),
        vibe_label=label,
        breakdown={"entrance": 65, "main_floor": 72, "checkout": 58},
    )


@vibe_router.get("/trend")
async def vibe_trend(hours: int = 24, current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    return [
        {"hour": h, "score": round(random.uniform(40, 85), 1), "label": random.choice(["Calm", "Steady", "Energetic"])}
        for h in range(hours)
    ]


# --- Demographics Router ---
demographics_router = APIRouter(prefix="/api/demographics", tags=["Demographics"])


@demographics_router.get("/current", response_model=DemographicData)
async def get_demographics(current_user: User = Depends(get_current_user)):
    return DemographicData(
        zone=None,
        age_distribution={"18-25": 35, "26-35": 45, "36-45": 28, "46-55": 18, "56+": 12},
        gender_distribution={"male": 72, "female": 66},
        total_count=138,
    )


# --- Peak Hours Router ---
peak_hours_router = APIRouter(prefix="/api/peak-hours", tags=["Peak Hours"])


@peak_hours_router.get("/today", response_model=PeakHoursSummary)
async def get_peak_hours(current_user: User = Depends(get_current_user)):
    hourly = [
        PeakHourData(hour=h, visitor_count=max(0, int(50 * (1 + 0.5 * random.gauss(0, 1)))),
                     avg_dwell_time=round(random.uniform(300, 1800), 0),
                     busiest_zone=random.choice(["Main Floor", "Electronics", "Food Court"]))
        for h in range(9, 22)
    ]
    peak = max(hourly, key=lambda x: x.visitor_count)
    return PeakHoursSummary(
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        peak_hour=peak.hour, peak_count=peak.visitor_count,
        total_visitors=sum(h.visitor_count for h in hourly),
        hourly_data=hourly,
    )


# --- Dashboard Overview Router ---
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@dashboard_router.get("/overview", response_model=DashboardOverview)
async def get_dashboard_overview(current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    score = round(random.uniform(55, 80), 1)
    return DashboardOverview(
        total_cameras=8,
        active_cameras=6,
        total_detections_today=random.randint(1500, 5000),
        current_occupancy=random.randint(30, 120),
        fire_alerts_today=random.randint(0, 3),
        avg_checkout_wait=round(random.uniform(60, 300), 0),
        store_vibe=StoreVibe(
            overall_score=score, sentiment_score=65.0, energy_score=70.0,
            engagement_score=58.0, foot_traffic_score=72.0,
            timestamp=now, vibe_label="Energetic",
        ),
        peak_hour_today=14,
        top_zone="Electronics",
    )
