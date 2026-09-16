"""
OmniTrack AI — Job persistence
═══════════════════════════════════════════════════════════════

Flushes live KPI state to PostgreSQL on an interval, and restores job
configuration on startup so the Jobs page is not empty after a restart.

WRITE MODEL (VisRax's): one row per track per flush, carrying that track's
CUMULATIVE counters. Reading totals therefore means "latest row per track, then
sum" — `_latest_per_track` below. Summing raw rows would multiply every count
by the number of flushes it survived.

Restored jobs come back as INACTIVE: their configuration and history are
preserved, but the video feed itself is not resumed — the pipeline has no frames
until someone starts it again.
"""

import time
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, select, text, update

from app.database import AsyncSessionLocal
from app.models.camera import Camera
from app.models.job import JobAlert, JobRun, LinePassingCount, RoiDwell


def _now_us() -> int:
    return int(time.time() * 1_000_000)


class JobPersistence:
    """Owns all job-related database traffic."""

    def __init__(self, pipeline: Any, flush_interval_s: float = 5.0) -> None:
        self._pipeline = pipeline
        self.flush_interval_s = flush_interval_s
        self._last_flush = 0.0
        self._failures = 0

    # ── camera row ────────────────────────────────────────────────
    @staticmethod
    async def ensure_camera(camera_id: int, name: str = "", zone: str = "", source: str = "") -> bool:
        """
        Guarantee a `cameras` row exists for this id.

        `detections.camera_id` is a foreign key to `cameras.id`, and the pipeline
        happily accepts a bare integer camera id that was never registered. Every
        detection insert then failed with ForeignKeyViolationError, swallowed at
        debug level — which is why nothing was ever written to the database.
        """
        try:
            async with AsyncSessionLocal() as db:
                existing = await db.get(Camera, camera_id)
                if existing is not None:
                    return False
                db.add(Camera(
                    id=camera_id,
                    name=name or f"Camera {camera_id}",
                    stream_url=(source or "")[:500] or "unknown",
                    zone=zone or "default",
                    is_active=True,
                ))
                await db.commit()
                logger.info(f"Registered cameras row for camera {camera_id}")
                return True
        except Exception as e:
            logger.warning(f"Could not ensure camera row {camera_id}: {e}")
            return False

    # ── job config ────────────────────────────────────────────────
    @staticmethod
    async def save_job(camera_id: int, job: Dict[str, Any]) -> None:
        meta = job.get("meta") or {}
        job_id = str(meta.get("job_id") or f"JOB-CAM{camera_id:02d}")
        try:
            async with AsyncSessionLocal() as db:
                # One active run per camera; supersede any previous one.
                await db.execute(
                    update(JobRun)
                    .where(JobRun.camera_id == camera_id, JobRun.is_active.is_(True))
                    .values(is_active=False)
                )
                db.add(JobRun(
                    job_id=job_id,
                    camera_id=camera_id,
                    activity_type=meta.get("activity_type"),
                    model=meta.get("model"),
                    tracker=meta.get("tracker"),
                    source=meta.get("source"),
                    zone=meta.get("zone"),
                    classes=job.get("classes") or [],
                    regions=job.get("regions") or [],
                    frame_width=job.get("frame_width") or 0,
                    frame_height=job.get("frame_height") or 0,
                    is_active=True,
                ))
                await db.commit()
                logger.info(f"Saved job {job_id} (camera {camera_id})")
        except Exception as e:
            logger.warning(f"Could not save job for camera {camera_id}: {e}")

    @staticmethod
    async def end_job(camera_id: int) -> None:
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(JobRun)
                    .where(JobRun.camera_id == camera_id, JobRun.is_active.is_(True))
                    .values(is_active=False, ended_at=text("now()"))
                )
                await db.commit()
        except Exception as e:
            logger.debug(f"Could not end job for camera {camera_id}: {e}")

    @staticmethod
    async def load_active_jobs() -> List[Dict[str, Any]]:
        """Job configs from the last run, for rehydrating the Jobs page."""
        try:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(JobRun).where(JobRun.is_active.is_(True)).order_by(JobRun.camera_id)
                )).scalars().all()
                return [{
                    "camera_id": r.camera_id,
                    "job_id": r.job_id,
                    "activity_type": r.activity_type,
                    "model": r.model,
                    "tracker": r.tracker,
                    "source": r.source,
                    "zone": r.zone,
                    "classes": r.classes or [],
                    "regions": r.regions or [],
                    "frame_width": r.frame_width or 0,
                    "frame_height": r.frame_height or 0,
                } for r in rows]
        except Exception as e:
            logger.warning(f"Could not load saved jobs: {e}")
            return []

    # ── KPI flush ─────────────────────────────────────────────────
    async def flush(self, force: bool = False) -> int:
        """Write every camera's current KPI state. Returns rows written."""
        now = time.time()
        if not force and (now - self._last_flush) < self.flush_interval_s:
            return 0
        self._last_flush = now

        at_us = _now_us()
        written = 0
        try:
            async with AsyncSessionLocal() as db:
                for camera_id, kpi in list(self._pipeline._camera_kpis.items()):
                    job = self._pipeline._camera_jobs.get(camera_id) or {}
                    meta = job.get("meta") or {}
                    job_id = str(meta.get("job_id") or f"JOB-CAM{camera_id:02d}")

                    if hasattr(kpi, "tracks") and hasattr(kpi, "totals_by_region"):
                        if isinstance(getattr(kpi, "tracks", None), dict):
                            written += self._stage_tracks(db, kpi, job_id, camera_id, at_us)
                if written:
                    await db.commit()
                    self._failures = 0
        except Exception as e:
            self._failures += 1
            if self._failures == 1 or self._failures % 20 == 0:
                logger.warning(f"Job flush failed ({self._failures}x): {e}")
            return 0
        return written

    def _stage_tracks(self, db, kpi, job_id: str, camera_id: int, at_us: int) -> int:
        """Queue one row per track. Shape depends on which KPI this is."""
        written = 0
        for track in kpi.tracks.values():
            data = track.as_dict()
            if "in_count" in data:
                db.add(LinePassingCount(
                    job_id=job_id,
                    camera_id=camera_id,
                    track_id=data["track_id"],
                    class_name=data.get("class_name"),
                    global_id=data.get("global_id"),
                    region_name=data.get("region_name"),
                    first_seen_in_region=data.get("first_seen_in_region"),
                    last_seen_in_region=data.get("last_seen_in_region"),
                    is_line_crossed=bool(data.get("is_line_crossed")),
                    in_count=data.get("in_count", 0),
                    out_count=data.get("out_count", 0),
                    left_count=data.get("left_count", 0),
                    right_count=data.get("right_count", 0),
                    at_us=at_us,
                ))
                written += 1
            elif "visited_regions" in data:
                dwell = data.get("region_dwell_us") or []
                db.add(RoiDwell(
                    job_id=job_id,
                    camera_id=camera_id,
                    track_id=data["track_id"],
                    class_name=data.get("class_name"),
                    global_id=data.get("global_id"),
                    region_name=data.get("region_name"),
                    visited_regions=",".join(data.get("visited_regions") or []),
                    region_dwell_us=",".join(str(d) for d in dwell),
                    current_region_dwell_us=data.get("current_region_dwell_us", 0),
                    total_dwell_us=sum(dwell),
                    at_us=at_us,
                ))
                written += 1
        return written

    # ── alerts ────────────────────────────────────────────────────
    @staticmethod
    async def save_alert(
        *, job_id: str, camera_id: int, alert_type: str, message: str,
        region_name: Optional[str] = None, track_id: Optional[int] = None,
        class_name: Optional[str] = None, value: Optional[float] = None,
        threshold: Optional[float] = None, snapshot_path: Optional[str] = None,
    ) -> None:
        try:
            async with AsyncSessionLocal() as db:
                db.add(JobAlert(
                    job_id=job_id, camera_id=camera_id, alert_type=alert_type,
                    region_name=region_name, track_id=track_id, class_name=class_name,
                    value=value, threshold=threshold, message=message,
                    snapshot_path=snapshot_path, at_us=_now_us(),
                ))
                await db.commit()
        except Exception as e:
            logger.debug(f"Could not save alert: {e}")

    @staticmethod
    async def recent_alerts(camera_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            async with AsyncSessionLocal() as db:
                stmt = select(JobAlert).order_by(JobAlert.at_us.desc()).limit(limit)
                if camera_id is not None:
                    stmt = stmt.where(JobAlert.camera_id == camera_id)
                rows = (await db.execute(stmt)).scalars().all()
                return [{
                    "id": r.id, "job_id": r.job_id, "camera_id": r.camera_id,
                    "alert_type": r.alert_type, "region_name": r.region_name,
                    "track_id": r.track_id, "class_name": r.class_name,
                    "value": r.value, "threshold": r.threshold, "message": r.message,
                    "at_us": r.at_us,
                } for r in rows]
        except Exception as e:
            logger.debug(f"Could not read alerts: {e}")
            return []

    # ── history ───────────────────────────────────────────────────
    @staticmethod
    async def line_summary(job_id: Optional[str] = None, camera_id: Optional[int] = None):
        """
        Per-region totals from history, using latest-row-per-track before summing
        (VisRax's `DISTINCT ON (track_id) ORDER BY at_us DESC`).
        """
        where, params = [], {}
        if job_id:
            where.append("job_id = :job_id"); params["job_id"] = job_id
        if camera_id is not None:
            where.append("camera_id = :camera_id"); params["camera_id"] = camera_id
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (track_id)
                   track_id, region_name, is_line_crossed,
                   in_count, out_count, left_count, right_count
            FROM line_passing_counts {clause}
            ORDER BY track_id, at_us DESC
        )
        SELECT region_name,
               COUNT(*) AS tracks,
               COUNT(*) FILTER (WHERE is_line_crossed) AS passed,
               COALESCE(SUM(in_count), 0)    AS in_count,
               COALESCE(SUM(out_count), 0)   AS out_count,
               COALESCE(SUM(left_count), 0)  AS left_count,
               COALESCE(SUM(right_count), 0) AS right_count
        FROM latest
        WHERE region_name IS NOT NULL AND region_name <> 'global'
        GROUP BY region_name ORDER BY region_name
        """
        try:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(text(sql), params)).mappings().all()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"line_summary failed: {e}")
            return []

    @staticmethod
    async def roi_summary(job_id: Optional[str] = None, camera_id: Optional[int] = None):
        where, params = [], {}
        if job_id:
            where.append("job_id = :job_id"); params["job_id"] = job_id
        if camera_id is not None:
            where.append("camera_id = :camera_id"); params["camera_id"] = camera_id
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (track_id)
                   track_id, region_name, total_dwell_us
            FROM roi_dwell {clause}
            ORDER BY track_id, at_us DESC
        )
        SELECT region_name,
               COUNT(*) AS unique_tracks,
               COALESCE(SUM(total_dwell_us), 0) / 1000000.0 AS total_dwell_seconds,
               COALESCE(AVG(total_dwell_us), 0) / 1000000.0 AS avg_dwell_seconds
        FROM latest
        WHERE region_name IS NOT NULL AND region_name <> 'global'
        GROUP BY region_name ORDER BY region_name
        """
        try:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(text(sql), params)).mappings().all()
                return [
                    {
                        "region_name": r["region_name"],
                        "unique_tracks": r["unique_tracks"],
                        "total_dwell_seconds": round(float(r["total_dwell_seconds"]), 2),
                        "avg_dwell_seconds": round(float(r["avg_dwell_seconds"]), 2),
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.debug(f"roi_summary failed: {e}")
            return []

    @staticmethod
    async def purge_job_history(camera_id: int) -> None:
        try:
            async with AsyncSessionLocal() as db:
                for model in (LinePassingCount, RoiDwell, JobAlert):
                    await db.execute(delete(model).where(model.camera_id == camera_id))
                await db.commit()
        except Exception as e:
            logger.debug(f"Could not purge history for camera {camera_id}: {e}")
