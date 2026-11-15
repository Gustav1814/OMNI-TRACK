"""
OmniTrack AI — Pipeline -> Database persistence.

Registered with ``ProcessingPipeline.on_results`` (called at most ~4 Hz with the
latest result per camera plus every Re-ID match / fire alert since the last call).
Each call writes, in one transaction:

  1. Detections: confirmed tracks only, at most one row per (camera, track) per
     ``DETECTION_SAMPLE_S`` (a 6 FPS pipeline would otherwise write 6 rows/s/person).
  2. Re-ID embeddings drained from the track coordinator (tracklet features of
     identities, tagged with the Re-ID weights' ``model_version``).
  3. Identity centroids (``reid_identities``) that changed since the last call.
  4. Foot-traffic samples (confirmed people per camera) every ``FOOT_INTERVAL_S``.
  5. Customer journey legs as global ids move across cameras.
  6. Humanless-store session updates.
  7. Fire/smoke incident start/reopen -> tamper-evident audit chain.

Zone events, store vibe and fire incidents are persisted by the analytics engine
(``app.analytics.zones_repo.persist_events``), not here.

Lifecycle events (pipeline/camera start/stop) are written to the audit chain; on
``pipeline_stopped`` the remaining embeddings, identities and open journey legs
are flushed so nothing is lost at shutdown.

If the DB is unavailable the callback logs (rate-limited) and the pipeline keeps
running for live viewing.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.crud import (
    AnalyticsService,
    AuditService,
    CameraService,
    DetectionService,
    EmbeddingService,
    ReIDIdentityService,
)
from app.services.humanless_store import HumanlessStoreService


class PersistencePipelineCallback:
    """Pipeline results -> PostgreSQL/pgvector. Also the lifecycle audit hook."""

    DETECTION_SAMPLE_S: float = 1.0
    FOOT_INTERVAL_S: float = 60.0
    JOURNEY_LEG_TIMEOUT_S: float = 90.0  # a leg ends when its global id is unseen this long
    TRACK_KEY_TTL_S: float = 120.0

    def __init__(self, pipeline, session_maker=AsyncSessionLocal, model_version: str | None = None):
        self._pipeline = pipeline
        self._session_maker = session_maker
        self._model_version = model_version or settings.reid_model_version

        self._last_foot_write: float = 0.0
        self._last_det_write: Dict[Tuple[int, int], float] = {}
        self._fire_keys_written: Dict[str, float] = {}
        # (global_id, camera_id, zone) -> (started_at_epoch, last_seen_epoch)
        self._active_legs: Dict[Tuple[str, int, str], Tuple[float, float]] = {}
        self._consecutive_failures: int = 0
        self._known_db_cameras: Set[int] = set()
        self.stats: Dict[str, int] = {
            "detections": 0, "embeddings": 0, "identities": 0, "journey_legs": 0, "fire_audits": 0,
        }

    # ── tick ────────────────────────────────────────────────────────
    async def __call__(self, results: Dict[int, Any], global_state: Any) -> None:
        try:
            async with self._session_maker() as db:
                try:
                    await self._persist_tick(db, results)
                    await db.commit()
                    self._consecutive_failures = 0
                except Exception:
                    await db.rollback()
                    raise
        except Exception as e:
            self._consecutive_failures += 1
            if self._consecutive_failures == 1 or self._consecutive_failures % 50 == 0:
                logger.warning(
                    f"Persistence skipped: {e} [consecutive_failures={self._consecutive_failures}]"
                )

    async def _persist_tick(self, db: AsyncSession, results: Dict[int, Any]) -> None:
        now = time.time()
        await self._ensure_db_cameras(db, results)
        await self._write_detections(db, results, now)
        await self._write_reid(db)

        if now - self._last_foot_write >= self.FOOT_INTERVAL_S:
            for cam_id, r in results.items():
                zone = self._zone(cam_id)
                await AnalyticsService.save_foot_traffic(
                    db, camera_id=int(cam_id), zone=zone, person_count=len(r.tracks or []),
                )
            self._last_foot_write = now

        await self._update_journey_legs(db, results, now)

        try:
            async with db.begin_nested():
                await HumanlessStoreService.process_pipeline_tick(
                    db, results=results, camera_zones=getattr(self._pipeline, "_camera_zones", {}),
                )
        except Exception as e:
            logger.warning(f"Humanless session update skipped: {e}")

        await self._audit_fire(db, results, now)

    def _zone(self, cam_id: int) -> str:
        return getattr(self._pipeline, "_camera_zones", {}).get(cam_id, "default")

    async def _write_detections(self, db: AsyncSession, results: Dict[int, Any], now: float) -> None:
        for cam_id, r in results.items():
            rows = []
            for det in r.detections or []:
                tid = det.get("track_id")
                key = (int(cam_id), int(tid) if tid is not None else -1)
                if now - self._last_det_write.get(key, 0.0) < self.DETECTION_SAMPLE_S:
                    continue
                self._last_det_write[key] = now
                rows.append(det)
            if rows:
                self.stats["detections"] += await DetectionService.create_batch(
                    db, int(cam_id), rows, zone=self._zone(cam_id)
                )
        if len(self._last_det_write) > 4096:
            cutoff = now - self.TRACK_KEY_TTL_S
            self._last_det_write = {k: t for k, t in self._last_det_write.items() if t >= cutoff}

    async def _write_reid(self, db: AsyncSession) -> None:
        pending = self._pipeline.get_and_clear_pending_embeddings()
        if pending:
            rows = [{
                "detection_id": None,
                "camera_id": e.get("camera_id"),
                "track_id": e.get("track_id"),
                "global_id": e.get("global_id"),
                "vector": e.get("embedding"),
                "confidence": e.get("confidence"),
            } for e in pending]
            self.stats["embeddings"] += await EmbeddingService.store_batch(
                db, rows, model_version=self._model_version
            )
        dirty = self._pipeline.get_dirty_identities()
        if dirty:
            self.stats["identities"] += await ReIDIdentityService.upsert_batch(
                db, dirty, model_version=self._model_version
            )

    async def _audit_fire(self, db: AsyncSession, results: Dict[int, Any], now: float) -> None:
        """One audit entry per incident start/reopen (the incident row itself is the engine's)."""
        for cam_id, r in results.items():
            for alert in r.fire_alerts or []:
                key = f"{alert.get('incident_key')}|{alert.get('action')}"
                if alert.get("incident_key") is None or key in self._fire_keys_written:
                    continue
                self._fire_keys_written[key] = now
                await AuditService.log_event(
                    db,
                    event_type="fire_alert",
                    user_id=None,
                    description=(
                        f"{alert.get('alert_type', 'fire/smoke')} incident {alert.get('action')} on camera "
                        f"{cam_id} (peak conf={float(alert.get('confidence') or 0):.2f})"
                    ),
                    metadata={
                        "camera_id": cam_id,
                        "incident_key": alert.get("incident_key"),
                        "action": alert.get("action"),
                        "alert_type": alert.get("alert_type"),
                        "confidence": alert.get("confidence"),
                        "bbox": alert.get("bbox"),
                        "zone": alert.get("zone"),
                        "timestamp": alert.get("timestamp"),
                    },
                )
                self.stats["fire_audits"] += 1
        if len(self._fire_keys_written) > 2000:
            cutoff = now - 86400.0
            self._fire_keys_written = {k: t for k, t in self._fire_keys_written.items() if t >= cutoff}

    def active_legs(self, global_id: str) -> List[Dict[str, Any]]:
        """Legs of ``global_id`` that are still open (not yet written to customer_journeys)."""
        return [
            {"camera_id": cam_id, "zone": zone, "started": started, "last_seen": last_seen}
            for (gid, cam_id, zone), (started, last_seen) in list(self._active_legs.items())
            if gid == global_id
        ]

    async def _update_journey_legs(
        self, db: AsyncSession, results: Dict[int, Any], now: float, flush_all: bool = False
    ) -> None:
        """Track (global_id, camera, zone) legs from Re-ID matches; write a leg once it
        has been unseen for ``JOURNEY_LEG_TIMEOUT_S`` (or on shutdown)."""
        for cam_id, r in results.items():
            zone = self._zone(cam_id)
            for match in r.reid_matches or []:
                gid = match.get("global_id")
                if not gid:
                    continue
                ts = float(match.get("timestamp") or now)
                key = (str(gid), int(cam_id), zone)
                started, last = self._active_legs.get(key, (ts, ts))
                self._active_legs[key] = (min(started, ts), max(last, ts))

        expired = [
            key for key, (_s, last_seen) in self._active_legs.items()
            if flush_all or (now - last_seen) > self.JOURNEY_LEG_TIMEOUT_S
        ]
        for key in expired:
            started, last_seen = self._active_legs.pop(key)
            gid, cam_id, zone = key
            await AnalyticsService.save_journey_leg(
                db,
                global_id=gid,
                camera_id=cam_id,
                zone=zone,
                entry_time=datetime.fromtimestamp(started, tz=timezone.utc),
                dwell_time=max(0.0, last_seen - started),
            )
            self.stats["journey_legs"] += 1

    # ── lifecycle (audit chain) ─────────────────────────────────────
    async def on_lifecycle(self, event: str, payload: Dict[str, Any]) -> None:
        description_map = {
            "pipeline_started": "Processing pipeline started",
            "pipeline_stopped": "Processing pipeline stopped",
            "camera_added": f"Camera added: {payload.get('camera_id')} ({payload.get('zone')})",
            "camera_removed": f"Camera removed: {payload.get('camera_id')}",
        }
        try:
            async with self._session_maker() as db:
                try:
                    if event == "pipeline_stopped":
                        await self._write_reid(db)
                        await self._update_journey_legs(db, {}, time.time(), flush_all=True)
                    await AuditService.log_event(
                        db,
                        event_type=event,
                        user_id=None,
                        description=description_map.get(event, event),
                        metadata=payload,
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
        except Exception as e:
            logger.warning(f"Lifecycle persistence failed for {event}: {e}")

    # ── gallery warm-up ─────────────────────────────────────────────
    async def warm_reid_gallery_from_db(self, max_rows: int | None = None) -> int:
        """Restore recent identities into the coordinator so global ids survive restarts.
        Returns the number of identities restored (the pipeline logs the count)."""
        limit = int(max_rows or settings.REID_WARM_START_ROWS)
        async with self._session_maker() as db:
            rows = await EmbeddingService.list_recent_gallery(
                db, max_rows=limit, model_version=self._model_version
            )
        return self._pipeline.warm_reid_gallery(rows)

    async def _ensure_db_cameras(self, db: AsyncSession, results: Dict[int, Any]) -> None:
        """Auto-register pipeline cameras so FK-constrained tables accept their rows."""
        streams = getattr(getattr(self._pipeline, "stream_manager", None), "_streams", {})
        for cam_id in results.keys():
            if cam_id in self._known_db_cameras:
                continue
            config = getattr(streams.get(cam_id), "config", None)
            source = getattr(config, "source", f"pipeline://camera/{cam_id}")
            fps = float(getattr(config, "fps_target", getattr(self._pipeline, "processing_fps", 6)))
            await CameraService.ensure_pipeline_camera(
                db, camera_id=int(cam_id), source=str(source), zone=self._zone(cam_id), fps=fps,
            )
            self._known_db_cameras.add(int(cam_id))

