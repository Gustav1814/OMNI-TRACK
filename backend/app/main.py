"""
OmniTrack AI — FastAPI Application Entry Point (PRODUCTION GRADE)
═══════════════════════════════════════════════════════════════════

This is the main file that starts everything:
  1. Connects to PostgreSQL + Redis on startup
  2. Loads all AI models
  3. Starts the multi-camera processing pipeline
  4. Registers all API routers
  5. Adds security middleware
  6. Handles WebSocket connections for live dashboard

TO RUN:
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

WHAT YOU NEED RUNNING:
  - PostgreSQL (with pgvector extension)
  - Redis (optional, falls back to in-memory)
  - Camera feeds OR test video files
"""

import asyncio
import logging
import os
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# Reduce verbose third-party startup noise (TensorFlow / torchreid warnings).
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
warnings.filterwarnings(
    "ignore",
    message="Cython evaluation .* unavailable.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*sparse_softmax_cross_entropy is deprecated.*",
    category=UserWarning,
)
logging.getLogger("tensorflow").setLevel(logging.ERROR)

# Database
from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import engine, Base, get_db, AsyncSessionLocal

# Middleware
from app.middleware import (
    RequestTracingMiddleware,
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
)

# Services
from app.services.cache import RedisCache
from app.services.broadcast import BroadcastService
from app.services.pipeline import ProcessingPipeline
from app.services.export import ExportService
from app.services.persistence import PersistencePipelineCallback
from app.services.event_bus import KafkaBus, RedisStreamBus, NullBus
from app.services.vector_store import PgVectorStore, QdrantStore, FaissStore
from app.services.memory_guard import MemoryGuard
from app.services.storage_guard import StorageGuard
from app.services.crud import CameraService

# Routers
from app.routers import auth, cameras, detection, reid, footage, model
from app.routers.analytics import (
    synopsis_router,
    shelf_router,
    fire_router,
    crowd_router,
    checkout_router,
    emotion_router,
    audit_router,
    vibe_router,
    demographics_router,
    peak_hours_router,
    dashboard_router,
)

# Config
from app.config import settings
from app.security.adversarial_eval import get_robustness_status
from app.security.dependencies import get_current_user
from app.models.user import User
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.embedding import Embedding
from app.models.analytics import FootTraffic


# ═══════════════════════════════════════════════════════════════
# GLOBAL SERVICE INSTANCES
# ═══════════════════════════════════════════════════════════════

# These are initialized once at startup and shared across the app
cache = RedisCache(settings.REDIS_URL)
broadcast = BroadcastService()
pipeline = ProcessingPipeline(
    detector_model=settings.YOLO_MODEL_PATH,
    reid_model=settings.REID_MODEL,
    fire_model=settings.FIRE_MODEL_PATH,
    confidence=settings.DETECTION_CONFIDENCE,
    device=settings.DEVICE,
    processing_fps=settings.PROCESSING_FPS,
    reid_threshold=getattr(settings, "REID_SIMILARITY_THRESHOLD", 0.6),
    reid_embeddings_per_id=getattr(settings, "REID_EMBEDDINGS_PER_ID", 5),
)
pipeline.set_broadcast(broadcast)
event_bus = NullBus()
vector_store = PgVectorStore()
memory_guard = MemoryGuard(pipeline, target_mb=getattr(settings, "MEMORY_GUARD_MB", 0))
storage_guard = StorageGuard(
    footage_dir=settings.FOOTAGE_DIR,
    min_free_mb=getattr(settings, "STORAGE_FREE_MIN_MB", 1024),
    retention_gb=getattr(settings, "FOOTAGE_RETENTION_GB", 20),
)

# Persistence callback — bridges every pipeline tick into PostgreSQL + pgvector
# + AES-256 / SHA-256 audit chain. This is what makes the backend PRODUCTION
# instead of simulation: detections, embeddings, foot traffic, vibe scores,
# journey legs, and fire alerts all flow into the database here.
persistence_callback = PersistencePipelineCallback(
    pipeline=pipeline,
    model_version=getattr(settings, "REID_MODEL", "osnet_x1_0"),
)


# ═══════════════════════════════════════════════════════════════
# LIFESPAN (Startup + Shutdown)
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager.
    
    ON STARTUP:
      1. Create DB tables
      2. Connect Redis cache
      3. Load AI models
      4. Log system status
    
    ON SHUTDOWN:
      1. Stop processing pipeline
      2. Disconnect Redis
      3. Close DB connections
    """
    startup_time = time.time()
    logger.info("═" * 60)
    logger.info("  OmniTrack AI — Starting Up")
    logger.info("═" * 60)

    # 1. Ensure pgvector extension (PostgreSQL only), then create database tables
    try:
        async with engine.begin() as conn:
            # Only create pgvector extension for PostgreSQL (not SQLite)
            if settings.DATABASE_URL.startswith("postgresql"):
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                logger.info("✅ pgvector extension enabled")
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables ready")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        logger.warning("   → Running without database (using mock data)")

    # 2. Connect Redis
    redis_ok = await cache.connect()
    if redis_ok:
        logger.info("✅ Redis connected")
    else:
        logger.warning("⚠️  Redis unavailable — using in-memory fallback")

    # 2b. Ensure storage dirs (local playback & exports)
    for d in [Path(settings.FOOTAGE_DIR), Path(settings.EXPORT_DIR)]:
        d.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ Storage dir ready: {d.resolve()}")

    # 2c. Configure pluggable event bus + vector store
    global event_bus, vector_store
    if getattr(settings, "EVENT_BUS_BACKEND", "redis").lower() == "kafka":
        event_bus = KafkaBus(getattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
        logger.info("⚙️ Event bus backend: kafka")
    elif redis_ok:
        event_bus = RedisStreamBus(settings.REDIS_URL)
        logger.info("⚙️ Event bus backend: redis-streams")
    else:
        event_bus = NullBus()
        logger.warning("⚠️ Event bus backend fallback: null")

    vector_backend = getattr(settings, "VECTOR_STORE_BACKEND", "pgvector").lower()
    if vector_backend == "qdrant":
        vector_store = QdrantStore(
            url=getattr(settings, "QDRANT_URL", "http://localhost:6333"),
            api_key=getattr(settings, "QDRANT_API_KEY", ""),
            collection_prefix=getattr(settings, "QDRANT_COLLECTION_PREFIX", "omnitrack_embeddings"),
        )
        logger.info("⚙️ Vector store backend: qdrant")
    elif vector_backend == "faiss":
        vector_store = FaissStore()
        logger.info("⚙️ Vector store backend: faiss")
    else:
        vector_store = PgVectorStore()
        logger.info("⚙️ Vector store backend: pgvector")

    pipeline.set_event_bus(event_bus)
    pipeline.set_vector_store(vector_store)
    pipeline.set_memory_guard(memory_guard)
    pipeline.storage_guard = storage_guard
    await memory_guard.start()
    await storage_guard.start_retention_worker()

    # Event bus -> websocket fanout (pluggable transport, single push path to UI)
    async def _bus_handler(msg):
        stream = msg.get("stream")
        payload = msg.get("payload") or {}
        if stream == "detections":
            await broadcast.broadcast("detections", "detection_update", payload)
        elif stream == "alerts":
            evt = payload.get("type") or payload.get("alert_type") or "fire_alert"
            await broadcast.broadcast("alerts", evt, payload)
        elif stream == "vibe":
            await broadcast.broadcast("vibe", "vibe_update", payload)

    try:
        await event_bus.subscribe("detections", group="omnitrack", consumer="api-1", handler=_bus_handler)
        await event_bus.subscribe("alerts", group="omnitrack", consumer="api-1", handler=_bus_handler)
        await event_bus.subscribe("vibe", group="omnitrack", consumer="api-1", handler=_bus_handler)
    except Exception as e:
        logger.debug(f"Event bus subscribe skipped: {e}")

    # 3. AI models are loaded lazily (on first use) to speed up startup

    # 3b. Warm Re-ID gallery from pgvector so cross-camera matching is
    #     warm across restarts (no cold-start false "new person" spikes).
    try:
        restored = await persistence_callback.warm_reid_gallery_from_db(
            max_rows=getattr(settings, "REID_GALLERY_WARM_LIMIT", 2000)
        )
        if restored:
            logger.info(f"✅ Restored {restored} Re-ID embeddings from pgvector")
    except Exception as e:
        logger.warning(f"⚠️  Re-ID gallery warm-up skipped: {e}")

    # 3c. Register persistence + lifecycle hooks so pipeline ticks
    #     (and camera/state transitions) are written to the DB.
    pipeline.on_results(persistence_callback)
    pipeline.on_lifecycle(persistence_callback.on_lifecycle)

    # 4. Store references for dependency injection
    app.state.cache = cache
    app.state.broadcast = broadcast
    app.state.pipeline = pipeline
    app.state.persistence = persistence_callback
    app.state.event_bus = event_bus
    app.state.vector_store = vector_store
    app.state.memory_guard = memory_guard
    app.state.storage_guard = storage_guard

    elapsed = time.time() - startup_time
    logger.info(f"✅ OmniTrack AI ready in {elapsed:.2f}s")
    logger.info("═" * 60)

    yield  # ← App runs here

    # Shutdown
    logger.info("🛑 Shutting down OmniTrack AI...")
    await pipeline.stop()
    await event_bus.close()
    await memory_guard.stop()
    await storage_guard.stop_retention_worker()
    await cache.disconnect()
    await engine.dispose()
    logger.info("Goodbye! 👋")


# ═══════════════════════════════════════════════════════════════
# CREATE APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="OmniTrack AI",
    description=(
        "Multi-camera AI surveillance analytics platform. "
        "Features: person detection, cross-camera Re-ID, "
        "emotion recognition, fire detection, crowd density, "
        "checkout analytics, shelf engagement, and Store Vibe Score."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ═══════════════════════════════════════════════════════════════
# MIDDLEWARE STACK (order matters — outermost runs first)
# ═══════════════════════════════════════════════════════════════

# CORS (allow dashboard to talk to API)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

# Request ID tracing + structured logging
app.add_middleware(RequestTracingMiddleware)

# Rate limiting
app.add_middleware(RateLimitMiddleware, cache=cache)


# ═══════════════════════════════════════════════════════════════
# REGISTER ROUTERS
# ═══════════════════════════════════════════════════════════════

app.include_router(auth.router)
app.include_router(cameras.router)
app.include_router(detection.router)
app.include_router(reid.router)
app.include_router(footage.router)
app.include_router(model.router)
# Analytics sub-routers (each has its own prefix in analytics.py)
app.include_router(synopsis_router)
app.include_router(shelf_router)
app.include_router(fire_router)
app.include_router(crowd_router)
app.include_router(checkout_router)
app.include_router(emotion_router)
app.include_router(audit_router)
app.include_router(vibe_router)
app.include_router(demographics_router)
app.include_router(peak_hours_router)
app.include_router(dashboard_router)


# ═══════════════════════════════════════════════════════════════
# DATABASE ADMIN PANEL  (/admin — admin-role users only)
# ═══════════════════════════════════════════════════════════════
try:
    from app.admin import setup_admin
    setup_admin(app)
    logger.info("✅ DB admin panel mounted at /admin")
except Exception as _admin_err:
    logger.warning(f"⚠️  Admin panel disabled: {_admin_err}")


# ═══════════════════════════════════════════════════════════════
# CORE ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/security/robustness", tags=["System"])
async def security_robustness():
    """
    Adversarial robustness status (proposal: ART, FGSM, PGD, documented resilience).
    Returns art_available, last_eval, and install hint. Run POST /api/security/robustness/run to execute eval.
    """
    return get_robustness_status()


@app.post("/api/security/robustness/run", tags=["System"])
async def security_robustness_run(
    sample_size: int = 4,
    eps_fgsm: float = 0.03,
    eps_pgd: float = 0.03,
    pgd_steps: int = 5,
    image_dir: str | None = None,
):
    """
    Run adversarial robustness evaluation (FGSM, PGD) via ART.
    Requires: pip install adversarial-robustness-toolbox[torch]

    If `image_dir` is provided, sample real images from that folder;
    otherwise uses the configured `FOOTAGE_DIR`, or random noise as a last resort.
    Returns YOLO person-detection counts before/after attacks.
    """
    from app.security.adversarial_eval import run_detector_robustness_eval
    result = run_detector_robustness_eval(
        sample_size=sample_size,
        eps_fgsm=eps_fgsm,
        eps_pgd=eps_pgd,
        pgd_steps=pgd_steps,
        image_dir=image_dir,
    )
    return result


@app.get("/api/health", tags=["System"])
async def health_check():
    """
    Comprehensive health check.
    Shows DB, Redis, AI models, and camera status.
    """
    # DB check
    db_status = "healthy"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"

    # Redis check
    redis_health = await cache.health()

    # Pipeline status
    pipeline_status = pipeline.get_status()

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "components": {
            "database": db_status,
            "redis": redis_health,
            "pipeline": {
                "state": pipeline_status["state"],
                "cameras_active": pipeline_status["cameras"]["active"],
                "cameras_total": pipeline_status["cameras"]["total"],
            },
            "ai_modules": pipeline_status["ai_modules"],
            "websocket": broadcast.stats,
        },
    }


@app.get("/api/system/profile", tags=["System"])
async def system_profile():
    status = pipeline.get_status()
    return {
        "runtime_profile": getattr(settings, "RUNTIME_PROFILE", "laptop"),
        "device": getattr(settings, "DEVICE", "auto"),
        "processing_fps": pipeline.processing_fps,
        "capture_decode_imgsz": getattr(settings, "DECODE_IMGSZ", 0),
        "event_bus_backend": getattr(settings, "EVENT_BUS_BACKEND", "redis"),
        "vector_store_backend": getattr(settings, "VECTOR_STORE_BACKEND", "pgvector"),
        "enable_mediapipe": getattr(settings, "ENABLE_MEDIAPIPE", True),
        "memory": status.get("runtime", {}).get("memory", {}),
        "max_cameras": getattr(settings, "MAX_CAMERAS", 16),
        "product_yolo": {
            "enabled": bool((getattr(settings, "PRODUCT_YOLO_PATH", None) or "").strip()),
            "every_n_frames": int(getattr(settings, "PRODUCT_DETECT_EVERY_N_FRAMES", 3)),
            "max_boxes": int(getattr(settings, "PRODUCT_MAX_DETECTIONS_PER_FRAME", 25)),
        },
    }


@app.get("/api/system/database-stats", tags=["System"])
async def database_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Row counts in Postgres + pipeline persistence counters.
    Use this when /admin tables look empty: confirms whether the pipeline is writing
    or whether no feed is running / inserts are failing.
    """
    _ = current_user
    n_cam = await db.scalar(select(func.count(Camera.id)))
    n_det = await db.scalar(select(func.count(Detection.id)))
    n_emb = await db.scalar(select(func.count(Embedding.id)))
    n_ft = await db.scalar(select(func.count(FootTraffic.id)))
    persist = getattr(request.app.state, "persistence", None)
    p_stats = persist.get_stats() if persist else {}
    st = pipeline.get_status()
    return {
        "table_row_counts": {
            "cameras": int(n_cam or 0),
            "detections": int(n_det or 0),
            "embeddings": int(n_emb or 0),
            "foot_traffic": int(n_ft or 0),
        },
        "persistence_since_process_start": p_stats,
        "pipeline": {
            "state": st.get("state"),
            "active_cameras": st.get("cameras", {}).get("active", 0),
            "total_cameras": st.get("cameras", {}).get("total", 0),
        },
        "hints": (
            "Embeddings require Re-ID enabled on the feed. All backends persist vectors to Postgres; "
            "faiss/qdrant add a secondary search index. Foot traffic buckets every 60s while the pipeline runs. "
            "Detections need visible people (or valid boxes) in the video."
        ),
    }


@app.get("/api/pipeline/status", tags=["Pipeline"])
async def get_pipeline_status():
    """Get detailed multi-camera pipeline status."""
    # Try cache first
    cached = await cache.get_pipeline_state()
    if cached:
        return cached
    
    status = pipeline.get_status()
    await cache.cache_pipeline_state(status)
    return status


@app.post("/api/pipeline/start", tags=["Pipeline"])
async def start_pipeline():
    """Start the multi-camera processing pipeline."""
    if pipeline.state.value == "running":
        raise HTTPException(400, "Pipeline already running")
    await pipeline.start()
    return {"status": "started", "cameras": pipeline.stream_manager.total_count}


@app.post("/api/pipeline/stop", tags=["Pipeline"])
async def stop_pipeline():
    """Stop the processing pipeline."""
    await pipeline.stop()
    return {"status": "stopped"}


@app.post("/api/pipeline/cameras/add", tags=["Pipeline"])
async def add_pipeline_camera(
    camera_id: int,
    source: str,
    stream_type: str = "rtsp",
    zone: str = "default",
    fps: int = 30,
    skip_frames: int = 1,
    tracker: str = "botsort.yaml",
    enable_reid: bool = True,
    enable_fire: bool = False,
    fire_model_path: Optional[str] = None,
):
    """
    Add a camera to the live processing pipeline.
    
    Example sources:
      - RTSP: rtsp://admin:password@192.168.1.100:554/stream
      - File:  /path/to/test_video.mp4
      - Webcam: 0  (device index)
    """
    try:
        async with AsyncSessionLocal() as db:
            await CameraService.ensure_for_pipeline(
                db,
                camera_id=camera_id,
                stream_url=source,
                zone=zone,
                fps=float(fps),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"ensure camera row before pipeline add failed: {e}")
    try:
        pipeline.add_camera(
            camera_id=camera_id,
            source=source,
            stream_type=stream_type,
            zone=zone,
            fps=fps,
            skip_frames=skip_frames,
            tracker_config=tracker,
            enable_reid=enable_reid,
            enable_fire=enable_fire,
            fire_model_path=(fire_model_path.strip() if fire_model_path and enable_fire else None),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "added", "camera_id": camera_id, "zone": zone, "tracker": tracker}


@app.get("/api/pipeline/results", tags=["Pipeline"])
async def get_latest_results(camera_id: int = None):
    """Get the latest processing results from all or a specific camera."""
    results = pipeline.get_latest_results(camera_id)
    if not results:
        return {"message": "No results yet — pipeline may not be running"}
    return results


# ═══════════════════════════════════════════════════════════════
# LIVE MJPEG STREAM — CCTV with detection overlay (for dashboard)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/stream/camera/{camera_id}/live", tags=["Stream"])
async def stream_camera_live(
    camera_id: int,
    current_user: User = Depends(get_current_user),
):
    """
    Live MJPEG stream of one camera with detection/tracking overlay.
    Use in dashboard: <img src="/api/stream/camera/1/live?token=JWT" /> or Bearer header.
    """
    async def generate():
        while True:
            jpeg = pipeline.get_latest_annotated_jpeg(camera_id)
            if jpeg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            await asyncio.sleep(1 / max(pipeline.processing_fps, 1))

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"},
    )


# ═══════════════════════════════════════════════════════════════
# EXPORT ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/export/detections", tags=["Export"])
async def export_detections(format: str = "csv"):
    """Export detection logs as CSV or JSON."""
    # TODO: Pull real data from DB via CRUD service
    sample = [
        {"id": 1, "camera_id": 1, "timestamp": "2026-02-21T09:00:00", "class_name": "person",
         "confidence": 0.92, "track_id": "T-001", "zone": "Entrance"},
    ]
    if format == "json":
        return ExportService.to_json(sample, "detections")
    return ExportService.detection_report(sample)


@app.get("/api/export/traffic", tags=["Export"])
async def export_traffic(format: str = "csv"):
    """Export foot traffic report."""
    sample = [{"date": "2026-02-21", "hour": 9, "zone": "Entrance", "person_count": 42,
               "direction_in": 35, "direction_out": 7}]
    if format == "json":
        return ExportService.to_json(sample, "traffic")
    return ExportService.traffic_report(sample)


# ═══════════════════════════════════════════════════════════════
# WEBSOCKET — Live Dashboard Feed
# ═══════════════════════════════════════════════════════════════

@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """
    Main WebSocket endpoint for live dashboard updates.
    
    Connect from frontend:
      const ws = new WebSocket("ws://localhost:8000/ws/live");
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log(data.type, data.data);
      };
    
    Event types sent:
      - detection_update: New detections per camera
      - fire_alert: Fire/smoke alerts
      - crowd_alert: Crowd density warnings
      - vibe_update: Store Vibe Score changes
      - reid_match: Cross-camera person match
    """
    await broadcast.subscribe(ws, channel="all")
    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await ws.receive_text()
            # Client can send subscription changes
            try:
                import json
                msg = json.loads(data)
                if msg.get("action") == "subscribe":
                    channel = msg.get("channel", "all")
                    await broadcast.subscribe(ws, channel)
                elif msg.get("action") == "ping":
                    await ws.send_text('{"type": "pong"}')
            except (json.JSONDecodeError, Exception):
                pass
    except WebSocketDisconnect:
        broadcast.unsubscribe_all(ws)
        logger.debug("WebSocket client disconnected")


@app.websocket("/ws/camera/{camera_id}")
async def websocket_camera(ws: WebSocket, camera_id: int):
    """
    Per-camera WebSocket feed.
    Only sends events for a specific camera.
    """
    channel = f"camera_{camera_id}"
    await broadcast.subscribe(ws, channel)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcast.unsubscribe_all(ws)


# ═══════════════════════════════════════════════════════════════
# GLOBAL EXCEPTION HANDLER
# ═══════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Catch-all exception handler.
    Never expose raw stack traces to the client in production.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(f"[{request_id}] Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again.",
            "request_id": request_id,
        },
    )


# ═══════════════════════════════════════════════════════════════
# PIPELINE → BROADCAST CALLBACK
# ═══════════════════════════════════════════════════════════════

async def _pipeline_to_websocket(results, global_state):
    """
    Called after every pipeline processing cycle.
    Keeps dashboard cache warm. Live pushes are routed via EventBus subscribers.
    """
    # Cache for dashboard
    await cache.cache_pipeline_state(pipeline.get_status())

# Register the callback
pipeline.on_results(_pipeline_to_websocket)
