"""
OmniTrack AI — Analytics Routers
Combined router for: synopsis, shelf, fire, crowd, checkout, emotion, audit, vibe.

Every router prefers LIVE data from the processing pipeline
(via `pipeline.get_analytics_snapshot()`), and falls back to
demo data only when no cameras have produced a result yet.
Audit endpoints always hit the DB (SHA-256 chain).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
import random
from app.database import get_db
from app.models.user import User
from app.security.dependencies import get_current_user
from app.services.crud import AuditService, AnalyticsService
from app.schemas.schemas import (
    SynopsisResponse, ShelfEngagement, ShelfZone, FireAlert, CrowdStatus,
    CheckoutMetrics, EmotionResult, EmotionZoneAggregation,
    AuditEntry, AuditChainStatus, StoreVibe, FootTrafficData,
    DemographicData, PeakHourData, PeakHoursSummary, DashboardOverview,
    CustomerJourneyResponse,
)


def _snapshot(request: Request) -> Dict[str, Any]:
    """Fetch live analytics snapshot from the pipeline."""
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return {}
    try:
        return pipeline.get_analytics_snapshot() or {}
    except Exception:
        return {}


# --- Synopsis Router ---
synopsis_router = APIRouter(prefix="/api/synopsis", tags=["Video Synopsis"])

# Synopsis job registry (kept in-process; promoted to DB if needed)
_SYNOPSIS_JOBS: Dict[int, Dict[str, Any]] = {}
_SYNOPSIS_NEXT_ID: Dict[str, int] = {"n": 1}


def _list_synopsis_dir() -> List[Dict[str, Any]]:
    from pathlib import Path
    from app.config import settings as _settings

    out_dir = Path(_settings.EXPORT_DIR) / "synopsis"
    out_dir.mkdir(parents=True, exist_ok=True)
    items: List[Dict[str, Any]] = []
    for f in out_dir.iterdir():
        if f.is_file() and f.suffix.lower() in {".mp4", ".avi"}:
            try:
                stat = f.stat()
                items.append({"filename": f.name, "path": str(f.resolve()),
                              "size_bytes": stat.st_size, "mtime": stat.st_mtime})
            except OSError:
                continue
    items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return items


def _resolve_synopsis_input(camera_id: int, source: Optional[str]) -> str:
    """Pick a real video file to run synopsis against.
    Order of preference: explicit source, latest camera recording, latest footage upload.
    """
    from pathlib import Path
    from app.config import settings as _settings

    if source:
        p = Path(source)
        if p.is_file():
            return str(p.resolve())
        under = Path(_settings.FOOTAGE_DIR) / source
        if under.is_file():
            return str(under.resolve())
        raise HTTPException(404, f"Source not found: {source}")
    footage_dir = Path(_settings.FOOTAGE_DIR)
    if not footage_dir.is_dir():
        raise HTTPException(404, "No footage directory — upload a clip via /api/footage/upload")
    candidates = [
        f for f in footage_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".mp4", ".avi", ".mkv", ".webm", ".mov"}
        and (f"camera_{camera_id}_" in f.name or f.stem.startswith(f"{camera_id}_"))
    ]
    if not candidates:
        # Fall back to the latest footage regardless of camera
        candidates = [
            f for f in footage_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {".mp4", ".avi", ".mkv", ".webm", ".mov"}
        ]
    if not candidates:
        raise HTTPException(
            404, f"No footage found for camera {camera_id}. Upload an MP4 via /api/footage/upload or set `source=`."
        )
    candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


async def _run_synopsis_job(job_id: int, input_path: str, output_path: str, compression: float) -> None:
    """Background task that runs VideoSynopsis and updates the job record."""
    import asyncio as _asyncio
    from app.ai.synopsis import VideoSynopsis

    job = _SYNOPSIS_JOBS.get(job_id)
    if job is None:
        return
    try:
        job["status"] = "running"
        engine = VideoSynopsis(compression_target=compression)
        result = await _asyncio.to_thread(engine.process_video, input_path, output_path)
        job.update(result)
        job["status"] = "completed"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)


@synopsis_router.get("/", response_model=List[SynopsisResponse])
async def list_synopses(current_user: User = Depends(get_current_user)):
    """List completed synopsis outputs (written to exports/synopsis)."""
    items = _list_synopsis_dir()
    completed_jobs = [
        j for j in _SYNOPSIS_JOBS.values() if j.get("status") == "completed"
    ]
    responses: List[SynopsisResponse] = []
    for j in completed_jobs:
        responses.append(SynopsisResponse(
            id=j["id"], camera_id=j["camera_id"],
            original_duration=float(j.get("original_duration", 0)),
            synopsis_duration=float(j.get("synopsis_duration", 0)),
            compression_ratio=float(j.get("compression_ratio", 0)),
            output_path=j.get("output_path", ""),
            status=j.get("status", "completed"),
        ))
    if responses:
        return responses
    # Fall back: surface any files that exist on disk
    return [
        SynopsisResponse(
            id=i + 1, camera_id=0,
            original_duration=0.0, synopsis_duration=0.0,
            compression_ratio=0.0, output_path=it["path"], status="completed",
        )
        for i, it in enumerate(items)
    ]


@synopsis_router.post("/generate")
async def generate_synopsis(
    camera_id: int,
    hours: int = 1,
    source: Optional[str] = None,
    compression: float = 10.0,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a video synopsis for the given camera.
    `source` may be a full path or a footage filename; if omitted, the most
    recent recording for this camera is used.
    """
    from pathlib import Path
    import asyncio as _asyncio
    from app.config import settings as _settings

    input_path = _resolve_synopsis_input(camera_id, source)
    out_dir = Path(_settings.EXPORT_DIR) / "synopsis"
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = _SYNOPSIS_NEXT_ID["n"]
    _SYNOPSIS_NEXT_ID["n"] += 1
    output_path = str((out_dir / f"synopsis_cam{camera_id}_{job_id}.mp4").resolve())
    _SYNOPSIS_JOBS[job_id] = {
        "id": job_id, "camera_id": camera_id, "status": "queued",
        "input_path": input_path, "output_path": output_path,
    }
    _asyncio.create_task(_run_synopsis_job(job_id, input_path, output_path, compression))
    return {"job_id": job_id, "camera_id": camera_id, "status": "queued",
            "input": input_path, "output": output_path}


@synopsis_router.get("/jobs/{job_id}")
async def synopsis_job_status(
    job_id: int,
    current_user: User = Depends(get_current_user),
):
    job = _SYNOPSIS_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"Synopsis job {job_id} not found")
    return job


# --- Shelf Router ---
shelf_router = APIRouter(prefix="/api/shelf", tags=["Shelf Analytics"])


@shelf_router.post("/zones", status_code=201)
async def register_shelf_zone(
    zone: ShelfZone,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Register a shelf ROI in **pixel coordinates** for a camera (same space as YOLO boxes).
    Bbox is [x1, y1, x2, y2]; inverted drag corners are normalized server-side.
    """
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None or getattr(pipeline, "shelf_tracker", None) is None:
        raise HTTPException(status_code=503, detail="Shelf analytics is not enabled on this server")
    if len(zone.bbox) != 4:
        raise HTTPException(status_code=422, detail="bbox must have exactly 4 numbers [x1, y1, x2, y2]")
    from app.ai.shelf_analytics import ShelfZoneConfig

    pipeline.shelf_tracker.add_zone(
        ShelfZoneConfig(
            zone_id=zone.zone_id,
            zone_name=zone.zone_name,
            bbox=(
                float(zone.bbox[0]),
                float(zone.bbox[1]),
                float(zone.bbox[2]),
                float(zone.bbox[3]),
            ),
            camera_id=int(zone.camera_id),
        )
    )
    return {"ok": True, "zone_id": zone.zone_id, "camera_id": int(zone.camera_id)}


@shelf_router.get("/zones")
async def list_shelf_zones(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None or getattr(pipeline, "shelf_tracker", None) is None:
        return []
    out = []
    for z in pipeline.shelf_tracker.zones:
        x1, y1, x2, y2 = z.bbox
        out.append({
            "zone_id": z.zone_id,
            "zone_name": z.zone_name,
            "camera_id": z.camera_id,
            "bbox": [x1, y1, x2, y2],
        })
    return out


@shelf_router.get("/engagement", response_model=List[ShelfEngagement])
async def get_shelf_engagement(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    rankings = (snapshot.get("shelf") or {}).get("rankings") or []
    if rankings:
        return [
            ShelfEngagement(
                zone_id=r.get("zone_id", f"zone-{i}"),
                zone_name=r.get("zone_name", f"Zone {i+1}"),
                avg_dwell_time=float(r.get("avg_dwell_time", 0)),
                visit_count=int(r.get("visit_count", 0)),
                engagement_score=float(r.get("engagement_score", 0)),
                rank=int(r.get("rank", i + 1)),
            )
            for i, r in enumerate(rankings)
        ]
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
async def get_top_zones(
    request: Request, n: int = 5,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    rankings = (snapshot.get("shelf") or {}).get("rankings") or []
    if rankings:
        return [
            {"zone_name": r.get("zone_name"), "engagement_score": r.get("engagement_score"), "rank": r.get("rank", i + 1)}
            for i, r in enumerate(rankings[:n])
        ]
    zones = ["Electronics", "Beauty", "Groceries", "Sports", "Home & Garden"]
    return [{"zone_name": z, "engagement_score": round(95 - i * 8, 1), "rank": i + 1} for i, z in enumerate(zones[:n])]


# --- Fire Router ---
fire_router = APIRouter(prefix="/api/fire", tags=["Fire & Smoke Detection"])


@fire_router.get("/alerts", response_model=List[Dict[str, Any]])
async def get_fire_alerts(
    request: Request, limit: int = 20,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    history = (snapshot.get("fire") or {}).get("history") or []
    if history:
        recent = history[-limit:]
        return [
            {
                "id": i,
                "alert_type": a.get("alert_type"),
                "confidence": a.get("confidence"),
                "camera_id": a.get("camera_id"),
                "zone": a.get("zone"),
                "timestamp": a.get("timestamp"),
                "status": "active",
                "bbox": a.get("bbox"),
            }
            for i, a in enumerate(recent)
        ]
    return []


@fire_router.get("/status")
async def fire_status(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    fire = snapshot.get("fire") or {}
    cameras = snapshot.get("cameras") or {}

    # Surface the underlying detector state so the Safety Watch UI can show
    # whether a fire-trained model is actually loaded.
    pipeline = getattr(request.app.state, "pipeline", None)
    detector = pipeline.any_fire_detector() if pipeline else None
    det_status: Dict[str, Any] = {}
    if detector is not None:
        try:
            det_status = detector.get_status() or {}
        except Exception:
            det_status = {}

    model_loaded = bool(det_status.get("is_loaded", False))
    is_fire_specific = bool(det_status.get("is_fire_specific", False))

    if not detector:
        system_status = "disabled"
    elif not model_loaded:
        system_status = "model_not_loaded"
    elif not is_fire_specific:
        system_status = "generic_model_suppressed"
    elif fire.get("active"):
        system_status = "alert"
    else:
        system_status = "monitoring"

    return {
        "active_alerts": len(fire.get("active_alerts") or []),
        "total_today": len(fire.get("history") or []),
        "system_status": system_status,
        "cameras_covered": cameras.get("active", 0),
        "model_loaded": model_loaded,
        "is_fire_specific": is_fire_specific,
        "model_path": det_status.get("model_path"),
        "classes": det_status.get("classes") or {},
        "confidence_threshold": det_status.get("confidence_threshold"),
        "history_size": det_status.get("history_size", 0),
    }


# --- Crowd Router ---
crowd_router = APIRouter(prefix="/api/crowd", tags=["Crowd Density"])


@crowd_router.get("/status", response_model=List[CrowdStatus])
async def get_crowd_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns live per-zone crowd state from the pipeline. If the pipeline
    snapshot is empty (e.g. just restarted), falls back to the latest stored
    FootTraffic rows from the DB. Returns an empty list if neither source has
    data — never returns seeded/demo zones.
    """
    snapshot = _snapshot(request)
    zones = (snapshot.get("crowd") or {}).get("zones") or []
    if zones:
        return [
            CrowdStatus(
                zone=z.get("zone", f"cam-{z.get('camera_id', 0)}"),
                person_count=int(z.get("person_count", 0)),
                density=float(z.get("density", 0)),
                classification=z.get("classification", "empty"),
                threshold=float(z.get("max_capacity", z.get("area_sqm", 50))),
                camera_id=int(z.get("camera_id", 0)),
            )
            for z in zones
        ]

    # Stored fallback — latest FootTraffic per zone in the last hour.
    try:
        rows = await AnalyticsService.get_latest_zone_counts(db, within_minutes=60)
    except Exception:
        rows = []
    if not rows:
        return []

    def _classify(count: int) -> str:
        if count >= 40:
            return "critical"
        if count >= 25:
            return "high"
        if count >= 10:
            return "medium"
        if count > 0:
            return "low"
        return "empty"

    return [
        CrowdStatus(
            zone=r["zone"],
            person_count=r["person_count"],
            density=round(r["person_count"] / 50.0, 3),
            classification=_classify(r["person_count"]),
            threshold=50.0,
            camera_id=int(r["camera_id"]),
        )
        for r in rows
    ]


@crowd_router.get("/history/{zone}")
async def crowd_history(
    zone: str, request: Request, limit: int = 24,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline and pipeline.crowd and pipeline.crowd.history.get(zone):
        history = pipeline.crowd.get_zone_history(zone, limit=limit)
        return [
            {"hour": i, "count": h.get("count", 0), "classification": h.get("classification", "empty")}
            for i, h in enumerate(history)
        ]
    # Stored fallback — today's hourly FootTraffic rows for this zone.
    try:
        rows = await AnalyticsService.get_hourly_traffic(db, zone=zone)
    except Exception:
        rows = []

    def _classify(count: int) -> str:
        if count >= 40:
            return "critical"
        if count >= 25:
            return "high"
        if count >= 10:
            return "medium"
        if count > 0:
            return "low"
        return "empty"

    return [
        {
            "hour": int(r["hour"]),
            "count": int(r["count"]),
            "classification": _classify(int(r["count"])),
        }
        for r in rows[-limit:]
    ]


# --- Checkout Router ---
checkout_router = APIRouter(prefix="/api/checkout", tags=["Checkout Analytics"])


@checkout_router.get("/metrics", response_model=List[CheckoutMetrics])
async def get_checkout_metrics(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    lanes = (snapshot.get("checkout") or {}).get("lanes") or []
    if lanes:
        return [
            CheckoutMetrics(
                lane_id=str(l.get("lane_id", f"lane-{i}")),
                queue_length=int(l.get("queue_length", 0)),
                avg_service_time=float(l.get("avg_service_time", 0)),
                throughput=float(l.get("throughput", 0)),
                current_wait_estimate=float(l.get("current_wait_estimate", 0)),
                camera_id=int(l.get("camera_id", 0)),
            )
            for i, l in enumerate(lanes)
        ]
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
async def checkout_summary(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    summary = (snapshot.get("checkout") or {}).get("summary") or {}
    if summary.get("total_lanes"):
        lanes = (snapshot.get("checkout") or {}).get("lanes") or []
        busiest = max(lanes, key=lambda l: l.get("queue_length", 0), default=None)
        return {
            "total_lanes": summary.get("total_lanes", 0),
            "active_lanes": sum(1 for l in lanes if l.get("queue_length", 0) > 0),
            "total_served_today": summary.get("total_served", 0),
            "avg_service_time": summary.get("overall_avg_service_time", 0),
            "avg_wait_time": summary.get("overall_avg_service_time", 0),
            "busiest_lane": busiest.get("lane_id") if busiest else None,
        }
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
async def get_current_emotions(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    per_zone = (snapshot.get("emotions") or {}).get("per_zone") or []
    if per_zone:
        return [
            EmotionZoneAggregation(
                zone=e.get("zone") or f"cam-{e.get('camera_id', 0)}",
                dominant_emotion=e.get("dominant_emotion") or "neutral",
                emotion_distribution=e.get("emotion_distribution") or {},
                sample_count=int(e.get("sample_count", 0)),
                sentiment_score=float(e.get("sentiment_score", 0)),
            )
            for e in per_zone
        ]
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
async def store_sentiment(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    emo = snapshot.get("emotions") or {}
    per_zone = emo.get("per_zone") or []
    if per_zone:
        dom_counts: Dict[str, int] = {}
        for z in per_zone:
            key = z.get("dominant_emotion") or "neutral"
            dom_counts[key] = dom_counts.get(key, 0) + int(z.get("sample_count", 0))
        dominant = max(dom_counts, key=dom_counts.get) if dom_counts else "neutral"
        return {
            "overall_sentiment": emo.get("overall_sentiment", 0),
            "dominant_emotion": dominant,
            "total_faces_analyzed": emo.get("samples", 0),
        }
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
async def get_current_vibe(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    vibe = snapshot.get("vibe") or {}
    if vibe.get("overall_score") is not None:
        zones = (snapshot.get("crowd") or {}).get("zones") or []
        breakdown = {z.get("zone"): z.get("person_count", 0) for z in zones} or None
        return StoreVibe(
            overall_score=float(vibe.get("overall_score", 0)),
            sentiment_score=float(vibe.get("sentiment_score", 0)),
            energy_score=float(vibe.get("energy_score", 0)),
            engagement_score=float(vibe.get("engagement_score", 0)),
            foot_traffic_score=float(vibe.get("foot_traffic_score", 0)),
            timestamp=datetime.now(timezone.utc),
            vibe_label=vibe.get("vibe_label", "Steady"),
            breakdown=breakdown,
        )
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
async def vibe_trend(
    request: Request,
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline and pipeline.vibe_engine and pipeline.vibe_engine.history:
        trend = pipeline.vibe_engine.get_trend(limit=hours)
        return [
            {"hour": i, "score": t.get("overall"), "label": t.get("label")}
            for i, t in enumerate(trend)
        ]
    try:
        rows = await AnalyticsService.get_vibe_trend(db, hours=hours)
        if rows:
            return [
                {
                    "hour": r.timestamp.isoformat() if r.timestamp else None,
                    "score": float(r.overall_score or 0),
                    "label": r.vibe_label or "Steady",
                }
                for r in rows
            ]
    except Exception:
        pass
    return [
        {"hour": h, "score": round(random.uniform(40, 85), 1), "label": random.choice(["Calm", "Steady", "Energetic"])}
        for h in range(hours)
    ]


# --- Demographics Router ---
demographics_router = APIRouter(prefix="/api/demographics", tags=["Demographics"])


@demographics_router.get("/current", response_model=DemographicData)
async def get_demographics(
    zone: Optional[str] = None,
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Age/gender breakdown for the last `hours` of live demographic snapshots."""
    try:
        breakdown = await AnalyticsService.get_demographics_breakdown(
            db, hours=hours, zone=zone
        )
        if breakdown and breakdown.get("total_count", 0) > 0:
            return DemographicData(
                zone=breakdown.get("zone"),
                age_distribution=breakdown.get("age_distribution") or {},
                gender_distribution=breakdown.get("gender_distribution") or {},
                total_count=int(breakdown.get("total_count", 0)),
            )
    except Exception:
        pass
    # Cold-start fallback
    return DemographicData(
        zone=zone,
        age_distribution={"18-25": 35, "26-35": 45, "36-45": 28, "46-55": 18, "56+": 12},
        gender_distribution={"male": 72, "female": 66},
        total_count=138,
    )


# --- Peak Hours Router ---
peak_hours_router = APIRouter(prefix="/api/peak-hours", tags=["Peak Hours"])


@peak_hours_router.get("/today", response_model=PeakHoursSummary)
async def get_peak_hours(
    zone: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Peak-hours today derived from persisted FootTraffic rows."""
    try:
        today = datetime.now(timezone.utc)
        rows = await AnalyticsService.get_hourly_traffic(db, zone=zone, date=today)
        if rows:
            # Enrich with busiest zone per hour
            hourly: List[PeakHourData] = []
            for r in rows:
                hour = int(r["hour"])
                busiest = None
                if not zone:
                    try:
                        busiest = await AnalyticsService.get_busiest_zone_for_hour(
                            db, hour=hour, date=today
                        )
                    except Exception:
                        busiest = None
                hourly.append(PeakHourData(
                    hour=hour,
                    visitor_count=int(r["count"]),
                    avg_dwell_time=float(r.get("avg_dwell_time") or 0.0),
                    busiest_zone=busiest or zone or "Unknown",
                ))
            peak = max(hourly, key=lambda x: x.visitor_count)
            return PeakHoursSummary(
                date=today.strftime("%Y-%m-%d"),
                peak_hour=peak.hour, peak_count=peak.visitor_count,
                total_visitors=sum(h.visitor_count for h in hourly),
                hourly_data=hourly,
            )
    except Exception:
        pass
    # Cold-start fallback
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
async def get_dashboard_overview(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    snapshot = _snapshot(request)
    now = datetime.now(timezone.utc)

    cameras = snapshot.get("cameras") or {}
    detections = snapshot.get("detections") or {}
    crowd = snapshot.get("crowd") or {}
    fire = snapshot.get("fire") or {}
    checkout = snapshot.get("checkout") or {}
    vibe = snapshot.get("vibe") or {}

    if cameras.get("total"):
        # Live data path
        lanes = checkout.get("lanes") or []
        avg_wait = (
            sum(float(l.get("current_wait_estimate", 0)) for l in lanes) / len(lanes)
            if lanes else 0.0
        )
        top_zone = None
        zones = crowd.get("zones") or []
        if zones:
            top_zone = max(zones, key=lambda z: z.get("person_count", 0)).get("zone")

        store_vibe = StoreVibe(
            overall_score=float(vibe.get("overall_score", 0) or 0),
            sentiment_score=float(vibe.get("sentiment_score", 0) or 0),
            energy_score=float(vibe.get("energy_score", 0) or 0),
            engagement_score=float(vibe.get("engagement_score", 0) or 0),
            foot_traffic_score=float(vibe.get("foot_traffic_score", 0) or 0),
            timestamp=now,
            vibe_label=vibe.get("vibe_label", "Steady"),
        )
        return DashboardOverview(
            total_cameras=int(cameras.get("total", 0)),
            active_cameras=int(cameras.get("active", 0)),
            total_detections_today=int(detections.get("total_processed", 0)),
            current_occupancy=int(crowd.get("total_occupancy", 0)),
            fire_alerts_today=len(fire.get("history") or []),
            avg_checkout_wait=round(avg_wait, 1),
            store_vibe=store_vibe,
            peak_hour_today=now.hour,
            top_zone=top_zone,
        )

    # Cold-start fallback
    score = round(random.uniform(55, 80), 1)
    return DashboardOverview(
        total_cameras=0,
        active_cameras=0,
        total_detections_today=0,
        current_occupancy=0,
        fire_alerts_today=0,
        avg_checkout_wait=0.0,
        store_vibe=StoreVibe(
            overall_score=score, sentiment_score=65.0, energy_score=70.0,
            engagement_score=58.0, foot_traffic_score=72.0,
            timestamp=now, vibe_label="Steady",
        ),
        peak_hour_today=now.hour,
        top_zone=None,
    )
