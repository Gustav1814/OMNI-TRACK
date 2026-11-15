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
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 3
  (live MJPEG streams never end on their own; without the timeout a reload or Ctrl+C waits forever)

WHAT YOU NEED RUNNING:
  - PostgreSQL (with pgvector extension)
  - Redis (optional, falls back to in-memory)
  - Camera feeds OR test video files
"""

import asyncio
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
import time
from datetime import datetime, timezone

# Reduce verbose third-party startup noise.
warnings.filterwarnings(
    "ignore",
    message="Cython evaluation .* unavailable.*",
    category=UserWarning,
)

# Database
from app.database import engine, AsyncSessionLocal
from app.db.maintenance import ensure_database_ready, get_database_health

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
from app.services.persistence import PersistencePipelineCallback
from app.services import model_selection as model_select

# Routers
from app.routers import auth, cameras, detection, reid, footage, model, humanless, setup
from app.routers.pipeline_health import router as pipeline_health_router
from app.routers import zones as zones_routes
from app.routers import alerts as alerts_routes, evidence as evidence_routes, export as export_routes
from app.routers.evaluation import router as evaluation_router
from app.routers.security import security_router, audit_router, audit_verify_loop
from app.routers.synopsis import router as synopsis_router
from app.routers.zone_analytics import (
    shelf_router,
    fire_router,
    crowd_router,
    checkout_router,
    emotion_router,
    vibe_router,
    demographics_router,
    peak_hours_router,
    dashboard_router,
)

# Config
from app.config import settings
from app.plugins import PluginContext, PluginManager
from app.security.dependencies import get_current_user, get_user_from_token, require_role
from app.security.input_safety import UnsafeInputError, validate_camera_source, validate_model_name
from app.security.route_policy import enforce_route_policy, public_endpoint
from app.security.secrets_check import check_secrets_or_raise
from app.models.user import User, UserRole
from app.admin import setup_admin


# ═══════════════════════════════════════════════════════════════
# GLOBAL SERVICE INSTANCES
# ═══════════════════════════════════════════════════════════════

# These are initialized once at startup and shared across the app
cache = RedisCache(settings.REDIS_URL)
broadcast = BroadcastService()
pipeline = ProcessingPipeline(
    device=settings.DEVICE,
    processing_fps=settings.PROCESSING_FPS,
    max_cameras=settings.MAX_CAMERAS,
    frame_buffer_size=settings.FRAME_BUFFER_SIZE,
)  # detector / Re-ID / specialist weights and thresholds come from settings
pipeline.attach_broadcast(broadcast)

# Persistence callback — bridges every pipeline tick into PostgreSQL + pgvector
# + AES-256 / SHA-256 audit chain. This is what makes the backend PRODUCTION
# instead of simulation: detections, embeddings, foot traffic, vibe scores,
# journey legs, and fire alerts all flow into the database here.
persistence_callback = PersistencePipelineCallback(
    pipeline=pipeline,
    model_version=settings.reid_model_version,
)

# Optional trusted integrations. Plugins are explicitly enabled via
# ENABLED_PLUGINS and can add routes plus non-blocking pipeline hooks.
plugin_manager = PluginManager(settings.ENABLED_PLUGINS)


async def _authenticate_websocket(ws: WebSocket) -> User | None:
    """Authenticate browser WebSockets using ?token=JWT before accept."""
    token = ws.query_params.get("token")
    async with AsyncSessionLocal() as db:
        return await get_user_from_token(token, db)


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

    # 0a. Refuse to start with default/short secrets (JWT, AES, audit HMAC, DB password).
    try:
        check_secrets_or_raise(settings)
    except RuntimeError as e:
        logger.error(f"❌ {e}")
        raise

    # 0. Fail loudly if any configured weight file is missing (no silent mocks).
    try:
        settings.check_weights_or_raise()
    except RuntimeError as e:
        logger.error(f"❌ {e}")
        raise
    if settings.ALLOW_MOCK_AI:
        logger.warning("⚠️  ALLOW_MOCK_AI=true — unavailable models will return FAKE output. Never use this outside tests.")
    logger.info(f"✅ Model weights present: {[str(p) for p in settings.configured_weight_paths().values()]}")

    # 1. Enterprise DB setup: wait, migrate, ANALYZE, retention
    db_info: dict = {}
    try:
        db_info = await ensure_database_ready()
        logger.info(
            f"✅ Database ready (revision={db_info.get('revision')}, "
            f"pgvector={db_info.get('pgvector')})"
        )
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        if settings.REQUIRE_DATABASE:
            raise
        logger.warning("   → Running without database (nothing will be persisted)")

    # 2. Connect Redis (retries; raises if REQUIRE_REDIS and unavailable)
    redis_ok = await cache.connect()
    if redis_ok:
        logger.info("✅ Redis connected")
    elif not settings.REQUIRE_REDIS:
        logger.warning("⚠️  Redis unavailable — using in-memory fallback")

    # 2b. Ensure storage dirs (local playback & exports)
    for d in [Path(settings.FOOTAGE_DIR), Path(settings.EXPORT_DIR), Path(settings.EVIDENCE_DIR)]:
        d.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ Storage dir ready: {d.resolve()}")

    # 3. AI models are loaded lazily (on first use) to speed up startup

    # 3b. Warm Re-ID gallery from pgvector so cross-camera matching is
    #     warm across restarts (no cold-start false "new person" spikes).
    try:
        await persistence_callback.warm_reid_gallery_from_db(max_rows=settings.REID_WARM_START_ROWS)
        await pipeline.load_reid_config_from_db()
    except Exception as e:
        if settings.REQUIRE_DATABASE:
            raise
        logger.warning(f"⚠️  Re-ID gallery warm-up skipped: {e}")

    # 3b2. Load drawn zones into the zone analytics engine.
    try:
        n_zones = await zones_routes.sync_all_zones()
        logger.info(f"✅ Zone analytics engine loaded {n_zones} zone(s)")
    except Exception as e:
        logger.error(f"❌ Could not load zones into the analytics engine: {e}")

    # 3c. Register persistence + lifecycle hooks so pipeline ticks
    #     (and camera/state transitions) are written to the DB.
    pipeline.on_results(persistence_callback)
    pipeline.on_lifecycle(persistence_callback.on_lifecycle)

    # 4. Store references for dependency injection
    app.state.cache = cache
    app.state.broadcast = broadcast
    app.state.pipeline = pipeline
    app.state.persistence = persistence_callback
    app.state.plugins = plugin_manager
    app.state.db_info = db_info

    plugin_context = PluginContext(
        app=app,
        settings=settings,
        cache=cache,
        broadcast=broadcast,
        pipeline=pipeline,
        persistence=persistence_callback,
    )
    if plugin_manager.enabled:
        await plugin_manager.startup(plugin_context)
        plugin_manager.register_pipeline_hooks(pipeline)
        logger.info(f"✅ Plugins active: {plugin_manager.loaded_names}")

    # 5. Tamper-evident audit chain: verify at startup, then every
    #    AUDIT_VERIFY_INTERVAL_HOURS; failures log an error + broadcast an alert.
    audit_task = asyncio.create_task(audit_verify_loop(), name="audit-verify")

    elapsed = time.time() - startup_time
    logger.info(f"✅ OmniTrack AI ready in {elapsed:.2f}s")
    logger.info("═" * 60)

    yield  # ← App runs here

    # Shutdown
    logger.info("🛑 Shutting down OmniTrack AI...")
    audit_task.cancel()
    if plugin_manager.enabled:
        await plugin_manager.shutdown(plugin_context)
    await pipeline.stop()
    pipeline.shutdown()
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
    # Deny-by-default: every route needs a user; mutating routes need admin
    # unless explicitly marked (see app/security/route_policy.py).
    dependencies=[Depends(enforce_route_policy)],
)

if plugin_manager.enabled:
    plugin_manager.load()
    plugin_manager.register_routes(app)

setup_admin(app)


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
app.include_router(humanless.router)
app.include_router(setup.router)
app.include_router(pipeline_health_router)
app.include_router(zones_routes.router)
app.include_router(alerts_routes.router)
app.include_router(evidence_routes.router)
app.include_router(export_routes.router)
app.include_router(evaluation_router)
# Analytics sub-routers (each has its own prefix in analytics.py)
app.include_router(synopsis_router)
app.include_router(shelf_router)
app.include_router(fire_router)
app.include_router(crowd_router)
app.include_router(checkout_router)
app.include_router(emotion_router)
app.include_router(audit_router)
app.include_router(security_router)
app.include_router(vibe_router)
app.include_router(demographics_router)
app.include_router(peak_hours_router)
app.include_router(dashboard_router)


# ═══════════════════════════════════════════════════════════════
# CORE ENDPOINTS
# ═══════════════════════════════════════════════════════════════

# Adversarial robustness: /api/security/robustness[/run] live in app/routers/security.py.

@app.get("/api/health", tags=["System"])
@public_endpoint
async def health_check():
    """
    Comprehensive health check.
    Shows DB, Redis, AI models, and camera status.
    """
    # DB check (pool stats, migration revision, pgvector HNSW)
    db_health = await get_database_health()
    db_status = db_health.get("status", "unhealthy")

    # Redis check
    redis_health = await cache.health()

    # Pipeline status
    pipeline_status = pipeline.get_status()

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "components": {
            "database": db_health,
            "redis": redis_health,
            "pipeline": {
                "state": pipeline_status["state"],
                "cameras_active": pipeline_status["cameras"]["active"],
                "cameras_total": pipeline_status["cameras"]["total"],
            },
            "ai_modules": pipeline_status["ai_modules"],
            "websocket": broadcast.stats,
            "plugins": {
                "enabled": plugin_manager.enabled,
                "loaded": plugin_manager.loaded_names,
            },
        },
    }


@app.get("/api/pipeline/status", tags=["Pipeline"])
async def get_pipeline_status(current_user: User = Depends(get_current_user)):
    """Get detailed multi-camera pipeline status."""
    status = pipeline.get_status()
    await cache.cache_pipeline_state(status)
    return status


@app.post("/api/pipeline/start", tags=["Pipeline"])
async def start_pipeline(current_user: User = Depends(require_role(UserRole.ADMIN))):
    """Start the multi-camera processing pipeline."""
    await cache.invalidate_pipeline_state()
    if pipeline.state.value == "running":
        status = pipeline.get_status()
        await cache.cache_pipeline_state(status)
        return {"status": "running", "cameras": pipeline.stream_manager.total_count, "state": status}
    await pipeline.start()
    status = pipeline.get_status()
    await cache.cache_pipeline_state(status)
    return {
        "status": "started" if status.get("state") == "running" else status.get("state", "idle"),
        "cameras": pipeline.stream_manager.total_count,
        "state": status,
    }


@app.post("/api/pipeline/stop", tags=["Pipeline"])
async def stop_pipeline(current_user: User = Depends(require_role(UserRole.ADMIN))):
    """Stop the processing pipeline."""
    await cache.invalidate_pipeline_state()
    await pipeline.stop()
    status = pipeline.get_status()
    await cache.cache_pipeline_state(status)
    return {"status": "stopped", "state": status}


@app.post("/api/pipeline/cameras/add", tags=["Pipeline"])
async def add_pipeline_camera(
    camera_id: int,
    source: str,
    stream_type: str = "rtsp",
    zone: str = "default",
    fps: int = settings.PROCESSING_FPS,
    skip_frames: int = settings.DEFAULT_SKIP_FRAMES,
    model: str = None,
    models: str = None,
    model_mode: str = "manual",
    enable_reid: bool = True,
    tracker: str = "bytetrack",
    loop: bool = False,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Add a camera to the live processing pipeline (admin). ``loop`` replays video files.

    Sources are validated (SSRF/path-traversal safe):
      - RTSP: rtsp://user:pass@192.168.1.100:554/stream
      - HTTP(S): only hosts in CAMERA_SOURCE_ALLOWLIST
      - File: a video inside VIDEO_SOURCE_DIRS (realpath-checked)
      - Webcam: 0 (device index; only when ALLOW_WEBCAM_SOURCES=true)
    Model names are resolved inside model_weight/ only (model_selection).
    """
    try:
        source = validate_camera_source(source)
    except UnsafeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    mode = (model_mode or "manual").strip().lower()
    try:
        # Phase 0: ensemble / auto_all modes are disabled -> 400.
        model_select.ensure_model_mode_allowed(model_mode=mode, model=model, models=models)
        selected_models, effective_model_mode = model_select.select_model_names(
            model_mode=mode,
            model=model,
            models=models,
            zone=zone,
            source=source,
        )
        # Weights are pickles: only bare names that resolve inside model_weight/.
        model_paths = [str(validate_model_name(n)) for n in (selected_models or [])]
    except UnsafeInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await cache.invalidate_pipeline_state()
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
            loop=loop,
        )
        if pipeline.state.value != "running":
            await pipeline.start()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    status = pipeline.get_status()
    await cache.cache_pipeline_state(status)
    return {
        "status": "added",
        "camera_id": camera_id,
        "zone": zone,
        "models": [pipeline.detector.name if pipeline.detector else model_select.person_model_name()],
        "model_mode": effective_model_mode,
        "requested_model_mode": mode,
        "reid_enabled": enable_reid,
        "tracker": tracker,
        "pipeline_state": status,
    }


@app.get("/api/pipeline/results", tags=["Pipeline"])
async def get_latest_results(
    camera_id: int = None,
    current_user: User = Depends(get_current_user),
):
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
        seq = 0
        while True:
            got = pipeline.get_latest_annotated_jpeg_seq(camera_id, seq)
            if got:
                seq, jpeg = got
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            await asyncio.sleep(0.5 / max(pipeline.processing_fps, 1))

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"},
    )


# Export endpoints (/api/export/*) live in app/routers/export.py.


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
    user = await _authenticate_websocket(ws)
    if user is None:
        await ws.close(code=1008, reason="Authentication required")
        return

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
    user = await _authenticate_websocket(ws)
    if user is None:
        await ws.close(code=1008, reason="Authentication required")
        return

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
    Pushes results to all connected WebSocket clients.
    """
    for cam_id, result in results.items():
        # Detection updates
        await broadcast.push_detection_update(
            cam_id,
            len(result.detections),
            result.tracks,
        )

        # Fire alerts
        for alert in result.fire_alerts:
            await broadcast.push_fire_alert(
                cam_id,
                alert.get("type", "fire"),
                alert.get("confidence", 0),
                alert.get("zone", f"cam-{cam_id}"),
            )

    # Vibe update
    if global_state.vibe_score > 0:
        await broadcast.push_vibe_update(
            global_state.vibe_score,
            "Energetic" if global_state.vibe_score > 60 else "Calm",
            global_state.zone_occupancy,
        )

    # Cache for dashboard
    await cache.cache_pipeline_state(pipeline.get_status())

# Register the callback
pipeline.on_results(_pipeline_to_websocket)
