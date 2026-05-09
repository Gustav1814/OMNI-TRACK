"""
OmniTrack AI — Pipeline → Database Persistence

This is the bridge that makes the backend PRODUCTION instead of SIMULATION.

Registered as a pipeline callback, it runs after every processing tick and:
  1. Persists detections (per-camera batched inserts)
  2. Persists Re-ID embeddings to pgvector (with detection_id join)
  3. Persists foot traffic aggregates (every `FOOT_INTERVAL_S`)
  4. Persists Store Vibe scores (every `VIBE_INTERVAL_S`)
  5. Persists customer journey legs as Re-ID global_ids move across zones
  6. Writes fire/smoke alerts into the SHA-256 audit chain
     with AES-256-encrypted metadata

The PersistencePipelineCallback also subscribes to pipeline lifecycle events
(pipeline_started / pipeline_stopped / camera_added / camera_removed) and
records them in the audit chain so operators can prove who touched what.

If the DB is unavailable the callback degrades silently (logged at DEBUG) so
the pipeline keeps running for live viewing even during an outage.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.services.crud import (
    AnalyticsService,
    AuditService,
    DetectionService,
    EmbeddingService,
)


class PersistencePipelineCallback:
    """
    Callable registered with `ProcessingPipeline.on_results`. Also emits
    audit log entries for pipeline lifecycle events (via `on_lifecycle`).
    """

    VIBE_INTERVAL_S: float = 60.0
    FOOT_INTERVAL_S: float = 60.0
    JOURNEY_LEG_TIMEOUT_S: float = 90.0  # Leg "ends" if a global_id hasn't been seen this long

    def __init__(
        self,
        pipeline,
        session_maker=AsyncSessionLocal,
        model_version: str = "osnet_x1_0",
    ):
        self._pipeline = pipeline
        self._session_maker = session_maker
        self._model_version = model_version

        self._last_vibe_write: float = 0.0
        self._last_foot_write: float = 0.0
        self._fire_keys_written: Set[str] = set()
        # (global_id, camera_id, zone) -> (started_at_epoch, last_seen_epoch)
        self._active_legs: Dict[Tuple[str, int, str], Tuple[float, float]] = {}
        # rolling counter of DB failures so we can log at WARNING once per N skips
        self._consecutive_failures: int = 0

    # ─────────────────────────────────────────────────────────────
    # Tick callback (invoked after every pipeline cycle)
    # ─────────────────────────────────────────────────────────────

    async def __call__(self, results: Dict[int, Any], global_state: Any) -> None:
        try:
            async with self._session_maker() as db:
                try:
                    await self._persist_tick(db, results, global_state)
                    await db.commit()
                    self._consecutive_failures = 0
                except Exception:
                    await db.rollback()
                    raise
        except Exception as e:
            self._consecutive_failures += 1
            if self._consecutive_failures == 1 or self._consecutive_failures % 50 == 0:
                logger.warning(
                    f"Persistence skipped (DB unavailable?): {e} "
                    f"[consecutive_failures={self._consecutive_failures}]"
                )

    async def _persist_tick(
        self,
        db: AsyncSession,
        results: Dict[int, Any],
        global_state: Any,
    ) -> None:
        now = time.time()
        pipeline = self._pipeline

        # ── 1. Detections ─────────────────────────────────────────
        detection_rows_by_cam: Dict[int, List[Any]] = {}
        for cam_id, r in results.items():
            if not r.detections:
                continue
            zone = pipeline._camera_zones.get(cam_id, "default")
            try:
                rows = await DetectionService.create_batch_and_return(
                    db, cam_id, r.detections, zone=zone
                )
                detection_rows_by_cam[cam_id] = rows
            except Exception as e:
                logger.debug(f"DetectionService batch failed for cam {cam_id}: {e}")

        # ── 2. Embeddings (pgvector) ──────────────────────────────
        pending = pipeline.get_and_clear_pending_embeddings()
        if pending:
            det_map: Dict[Tuple[int, Optional[int]], int] = {}
            for cam_id, det_rows in detection_rows_by_cam.items():
                for row in det_rows:
                    det_map[(cam_id, row.track_id)] = row.id
            emb_rows: List[Dict[str, Any]] = []
            for e in pending:
                det_id = det_map.get((e.get("camera_id"), e.get("track_id")))
                emb_rows.append({
                    "detection_id": det_id,
                    "camera_id": e.get("camera_id"),
                    "track_id": e.get("track_id"),
                    "global_id": e.get("global_id"),
                    "vector": e.get("embedding"),
                    "confidence": e.get("confidence"),
                })
            try:
                await EmbeddingService.store_batch(db, emb_rows, model_version=self._model_version)
            except Exception as e:
                logger.debug(f"EmbeddingService batch failed: {e}")

        # ── 3. Foot traffic (bucketed every FOOT_INTERVAL_S) ──────
        if now - self._last_foot_write >= self.FOOT_INTERVAL_S:
            for cam_id, r in results.items():
                status = r.crowd_status or {}
                if not status:
                    continue
                zone = status.get("zone") or pipeline._camera_zones.get(cam_id, "default")
                count = int(status.get("person_count", 0))
                try:
                    await AnalyticsService.save_foot_traffic(
                        db, camera_id=cam_id, zone=zone, person_count=count,
                    )
                except Exception as e:
                    logger.debug(f"FootTraffic save failed: {e}")
            self._last_foot_write = now

        # ── 4. Store Vibe score (bucketed every VIBE_INTERVAL_S) ──
        if now - self._last_vibe_write >= self.VIBE_INTERVAL_S:
            v = pipeline._latest_vibe
            if v:
                try:
                    await AnalyticsService.save_vibe_score(
                        db,
                        overall_score=float(v.get("overall_score", 0)),
                        sentiment_score=float(v.get("sentiment_score", 0)),
                        energy_score=float(v.get("energy_score", 0)),
                        engagement_score=float(v.get("engagement_score", 0)),
                        foot_traffic_score=float(v.get("foot_traffic_score", 0)),
                        label=str(v.get("vibe_label", "Steady")),
                        breakdown=dict(global_state.zone_occupancy),
                    )
                    self._last_vibe_write = now
                except Exception as e:
                    logger.debug(f"Vibe save failed: {e}")

        # ── 5. Customer journey legs ──────────────────────────────
        await self._update_journey_legs(db, results, now)

        # ── 6. Fire alerts → audit chain (SHA-256 + AES-256) ─────
        for cam_id, r in results.items():
            for alert in (r.fire_alerts or []):
                key = f"{alert.get('timestamp')}|{cam_id}|{alert.get('alert_type')}|{alert.get('confidence')}"
                if key in self._fire_keys_written:
                    continue
                self._fire_keys_written.add(key)
                if len(self._fire_keys_written) > 5000:
                    # Keep set bounded
                    self._fire_keys_written = set(list(self._fire_keys_written)[-2500:])
                try:
                    await AuditService.log_event(
                        db,
                        event_type="fire_alert",
                        user_id=None,
                        description=(
                            f"{alert.get('alert_type', 'fire/smoke')} detected on camera "
                            f"{cam_id} (conf={float(alert.get('confidence', 0)):.2f})"
                        ),
                        metadata={
                            "camera_id": cam_id,
                            "alert_type": alert.get("alert_type"),
                            "confidence": alert.get("confidence"),
                            "bbox": alert.get("bbox"),
                            "zone": alert.get("zone"),
                            "timestamp": alert.get("timestamp"),
                        },
                    )
                except Exception as e:
                    logger.debug(f"AuditService fire_alert failed: {e}")

    async def _update_journey_legs(
        self,
        db: AsyncSession,
        results: Dict[int, Any],
        now: float,
    ) -> None:
        """
        Maintain per-(global_id, camera, zone) leg start/end timestamps. When a
        leg is no longer seen for JOURNEY_LEG_TIMEOUT_S we flush it to the DB.
        """
        seen_keys: Set[Tuple[str, int, str]] = set()
        pipeline = self._pipeline
        for cam_id, r in results.items():
            zone = pipeline._camera_zones.get(cam_id, "default")
            for match in (r.reid_matches or []):
                gid = match.get("global_id")
                if not gid:
                    continue
                key = (gid, int(cam_id), zone)
                seen_keys.add(key)
                started, _ = self._active_legs.get(key, (now, now))
                self._active_legs[key] = (started, now)

        # Flush legs that haven't been updated this tick and are old enough
        expired = [
            key for key, (_started, last_seen) in self._active_legs.items()
            if key not in seen_keys and (now - last_seen) > self.JOURNEY_LEG_TIMEOUT_S
        ]
        for key in expired:
            started, last_seen = self._active_legs.pop(key)
            gid, cam_id, zone = key
            from datetime import datetime, timezone
            try:
                await AnalyticsService.save_journey_leg(
                    db,
                    global_id=gid,
                    camera_id=cam_id,
                    zone=zone,
                    entry_time=datetime.fromtimestamp(started, tz=timezone.utc),
                    dwell_time=max(0.0, last_seen - started),
                )
            except Exception as e:
                logger.debug(f"Journey leg save failed for {gid}: {e}")

    # ─────────────────────────────────────────────────────────────
    # Lifecycle hook (audit chain)
    # ─────────────────────────────────────────────────────────────

    async def on_lifecycle(self, event: str, payload: Dict[str, Any]) -> None:
        """
        Subscriber for pipeline lifecycle events. Writes a tamper-evident
        SHA-256 audit entry with AES-256-encrypted metadata.
        """
        description_map = {
            "pipeline_started": "Processing pipeline started",
            "pipeline_stopped": "Processing pipeline stopped",
            "camera_added": f"Camera added: {payload.get('camera_id')} ({payload.get('zone')})",
            "camera_removed": f"Camera removed: {payload.get('camera_id')}",
        }
        description = description_map.get(event, event)
        try:
            async with self._session_maker() as db:
                try:
                    await AuditService.log_event(
                        db,
                        event_type=event,
                        user_id=None,
                        description=description,
                        metadata=payload,
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
        except Exception as e:
            logger.debug(f"Lifecycle audit log failed for {event}: {e}")

    # ─────────────────────────────────────────────────────────────
    # Gallery warm-up
    # ─────────────────────────────────────────────────────────────

    async def warm_reid_gallery_from_db(self, max_rows: int = 2000) -> int:
        """
        Load recent embeddings into the in-memory Re-ID gallery so cross-camera
        identities survive backend restarts.
        """
        try:
            async with self._session_maker() as db:
                rows = await EmbeddingService.list_recent_gallery(db, max_rows=max_rows)
            return self._pipeline.warm_reid_gallery(rows)
        except Exception as e:
            logger.warning(f"Re-ID gallery warm-up failed (DB not ready?): {e}")
            return 0
