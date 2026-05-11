"""
OmniTrack AI — CRUD Services Layer
Async database operations for all models with proper error handling.

WHY THIS EXISTS:
  Routers should NOT talk to the database directly. This service layer
  is the middle-man: routers call services, services talk to the DB.
  This makes testing easier and keeps business logic out of HTTP handlers.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, and_, desc, text
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from loguru import logger

# Models
from app.models.user import User, UserRole
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.embedding import Embedding
from app.models.audit_log import AuditLog
from app.models.analytics import (
    FootTraffic, CustomerJourney, DemographicSnapshot,
    StoreVibeScore, PeakHoursData,
)

# Security
from app.security.jwt_handler import hash_password
from app.security.hashing import compute_hash, verify_chain
from app.security.encryption import encrypt_data


# ═══════════════════════════════════════════════════════════════
# USER CRUD
# ═══════════════════════════════════════════════════════════════

class UserService:
    """Async CRUD operations for User model."""

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        full_name: str = "",
        role: UserRole = UserRole.VIEWER,
    ) -> User:
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=role,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        logger.info(f"Created user: {username} (role={role.value})")
        return user

    @staticmethod
    async def list_all(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[User]:
        result = await db.execute(
            select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def update_role(db: AsyncSession, user_id: int, role: UserRole) -> Optional[User]:
        user = await UserService.get_by_id(db, user_id)
        if user:
            user.role = role
            await db.flush()
            await db.refresh(user)
        return user

    @staticmethod
    async def deactivate(db: AsyncSession, user_id: int) -> bool:
        user = await UserService.get_by_id(db, user_id)
        if user:
            user.is_active = False
            await db.flush()
            return True
        return False

    @staticmethod
    async def count(db: AsyncSession) -> int:
        result = await db.execute(select(func.count(User.id)))
        return result.scalar()


# ═══════════════════════════════════════════════════════════════
# CAMERA CRUD
# ═══════════════════════════════════════════════════════════════

class CameraService:
    """Async CRUD operations for Camera model."""

    @staticmethod
    async def get_by_id(db: AsyncSession, camera_id: int) -> Optional[Camera]:
        result = await db.execute(select(Camera).where(Camera.id == camera_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession, active_only: bool = False) -> List[Camera]:
        query = select(Camera).order_by(Camera.id)
        if active_only:
            query = query.where(Camera.is_active == True)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def create(db: AsyncSession, **kwargs) -> Camera:
        camera = Camera(**kwargs)
        db.add(camera)
        await db.flush()
        await db.refresh(camera)
        logger.info(f"Camera created: {camera.name} (zone={camera.zone})")
        return camera

    @staticmethod
    async def ensure_for_pipeline(
        db: AsyncSession,
        camera_id: int,
        stream_url: str,
        zone: Optional[str] = None,
        fps: float = 30.0,
    ) -> Camera:
        """
        Guarantee a `cameras` row for this id so detection/embedding/analytics FK inserts succeed.
        Called when a feed is registered without going through POST /api/cameras first.
        """
        from app.config import settings

        stream_url = (stream_url or "").strip() or "pipeline://unknown"
        stream_url = stream_url[:500]
        zone_val = ((zone or "default").strip()[:100] or "default")

        existing = await CameraService.get_by_id(db, camera_id)
        if existing:
            if stream_url and existing.stream_url != stream_url:
                existing.stream_url = stream_url
            if zone_val and existing.zone != zone_val:
                existing.zone = zone_val
            if fps is not None and float(fps) != float(existing.fps or 0):
                existing.fps = float(fps)
            await db.flush()
            await db.refresh(existing)
            return existing

        camera = Camera(
            id=camera_id,
            name=f"Camera {camera_id}",
            stream_url=stream_url,
            location=None,
            zone=zone_val,
            resolution_w=1920,
            resolution_h=1080,
            fps=float(fps),
            is_active=True,
            camera_type="general",
            roi_config=None,
        )
        db.add(camera)
        await db.flush()
        await db.refresh(camera)
        logger.info(f"Camera row auto-created for pipeline persistence: id={camera_id}")

        if str(settings.DATABASE_URL).startswith("postgresql"):
            try:
                await db.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence('cameras', 'id'), "
                        "(SELECT COALESCE(MAX(id), 1) FROM cameras))"
                    )
                )
            except Exception as e:
                logger.debug(f"cameras id sequence sync skipped: {e}")

        return camera

    @staticmethod
    async def update(db: AsyncSession, camera_id: int, **kwargs) -> Optional[Camera]:
        camera = await CameraService.get_by_id(db, camera_id)
        if camera:
            for key, val in kwargs.items():
                if hasattr(camera, key) and val is not None:
                    setattr(camera, key, val)
            await db.flush()
            await db.refresh(camera)
        return camera

    @staticmethod
    async def delete(db: AsyncSession, camera_id: int) -> bool:
        camera = await CameraService.get_by_id(db, camera_id)
        if camera:
            await db.delete(camera)
            await db.flush()
            return True
        return False

    @staticmethod
    async def count(db: AsyncSession, active_only: bool = False) -> int:
        query = select(func.count(Camera.id))
        if active_only:
            query = query.where(Camera.is_active == True)
        result = await db.execute(query)
        return result.scalar()


# ═══════════════════════════════════════════════════════════════
# DETECTION CRUD
# ═══════════════════════════════════════════════════════════════


def _scalar_int_or_none(val: Any) -> Optional[int]:
    """SQLAlchemy/asyncpg rejects numpy int64 for Integer columns."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


class DetectionService:
    """Async operations for saving and querying detections."""

    @staticmethod
    async def create_batch(
        db: AsyncSession,
        camera_id: int,
        detections: List[Dict[str, Any]],
        zone: Optional[str] = None,
    ) -> int:
        """
        Batch-insert detections from a processing frame.
        Returns count of inserted rows.
        """
        objects = []
        for det in detections:
            bbox = det.get("bbox") or det.get("box") or [0.0, 0.0, 0.0, 0.0]
            if len(bbox) < 4:
                continue
            objects.append(Detection(
                camera_id=camera_id,
                bbox_x=float(bbox[0]),
                bbox_y=float(bbox[1]),
                bbox_w=float(bbox[2]),
                bbox_h=float(bbox[3]),
                confidence=float(det.get("confidence", 0.0)),
                class_name=str(det.get("class_name") or det.get("class") or "person"),
                track_id=_scalar_int_or_none(det.get("track_id")),
                global_id=(str(det["global_id"]) if det.get("global_id") is not None else None),
                zone=det.get("zone") or zone,
            ))
        if not objects:
            return 0
        db.add_all(objects)
        await db.flush()
        return len(objects)

    @staticmethod
    async def create_batch_and_return(
        db: AsyncSession,
        camera_id: int,
        detections: List[Dict[str, Any]],
        zone: Optional[str] = None,
    ) -> List["Detection"]:
        """
        Same as create_batch but returns the inserted Detection rows so
        the caller can grab their IDs (used to join with Embedding rows).
        """
        objects = []
        for det in detections:
            bbox = det.get("bbox") or det.get("box") or [0.0, 0.0, 0.0, 0.0]
            if len(bbox) < 4:
                continue
            objects.append(Detection(
                camera_id=camera_id,
                bbox_x=float(bbox[0]),
                bbox_y=float(bbox[1]),
                bbox_w=float(bbox[2]),
                bbox_h=float(bbox[3]),
                confidence=float(det.get("confidence", 0.0)),
                class_name=str(det.get("class_name") or det.get("class") or "person"),
                track_id=_scalar_int_or_none(det.get("track_id")),
                global_id=(str(det["global_id"]) if det.get("global_id") is not None else None),
                zone=det.get("zone") or zone,
            ))
        if not objects:
            return []
        db.add_all(objects)
        await db.flush()
        for obj in objects:
            await db.refresh(obj)
        return objects

    @staticmethod
    async def get_recent(
        db: AsyncSession,
        camera_id: Optional[int] = None,
        limit: int = 100,
        hours: int = 24,
    ) -> List[Detection]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = select(Detection).where(Detection.timestamp >= cutoff)
        if camera_id:
            query = query.where(Detection.camera_id == camera_id)
        query = query.order_by(Detection.timestamp.desc()).limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def count_today(db: AsyncSession, camera_id: Optional[int] = None) -> int:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = select(func.count(Detection.id)).where(Detection.timestamp >= today)
        if camera_id:
            query = query.where(Detection.camera_id == camera_id)
        result = await db.execute(query)
        return result.scalar()


# ═══════════════════════════════════════════════════════════════
# EMBEDDING CRUD (Re-ID vectors)
# ═══════════════════════════════════════════════════════════════

class EmbeddingService:
    """Async operations for Re-ID embedding storage and search."""

    @staticmethod
    async def store(
        db: AsyncSession,
        detection_id: Optional[int],
        camera_id: int,
        embedding_vector: list,
        global_id: Optional[str] = None,
        track_id: Optional[int] = None,
        confidence: Optional[float] = None,
        model_version: str = "osnet_x1_0",
    ) -> Embedding:
        emb = Embedding(
            detection_id=detection_id,
            camera_id=camera_id,
            track_id=_scalar_int_or_none(track_id),
            global_id=global_id,
            vector=list(embedding_vector),
            confidence=confidence,
            model_version=model_version,
        )
        db.add(emb)
        await db.flush()
        return emb

    @staticmethod
    async def store_batch(
        db: AsyncSession,
        rows: List[Dict[str, Any]],
        model_version: str = "osnet_x1_0",
    ) -> int:
        """
        Batch-insert embedding rows. Each row: {
          detection_id?, camera_id, track_id?, global_id, vector: list, confidence?
        }
        """
        objs = []
        for r in rows:
            vec = r.get("vector")
            if not vec:
                continue
            gid = r.get("global_id")
            if gid is not None:
                gid = str(gid)
            objs.append(Embedding(
                detection_id=r.get("detection_id"),
                camera_id=int(r.get("camera_id", 0)),
                track_id=_scalar_int_or_none(r.get("track_id")),
                global_id=gid,
                vector=list(vec),
                confidence=r.get("confidence"),
                model_version=model_version,
            ))
        if not objs:
            return 0
        db.add_all(objs)
        await db.flush()
        return len(objs)

    @staticmethod
    async def list_recent_gallery(
        db: AsyncSession,
        max_rows: int = 2000,
    ) -> List[Dict[str, Any]]:
        """
        Warm up the in-memory Re-ID gallery from the most recent embeddings.
        Survives backend restart without losing cross-camera identities.
        """
        result = await db.execute(
            select(Embedding)
            .order_by(Embedding.timestamp.desc())
            .limit(max_rows)
        )
        rows = result.scalars().all()
        out = []
        for r in rows:
            if r.global_id and r.vector is not None:
                out.append({
                    "global_id": r.global_id,
                    "vector": list(r.vector),
                    "timestamp": r.timestamp,
                })
        return out

    @staticmethod
    async def get_by_global_id(
        db: AsyncSession, global_id: str
    ) -> List[Embedding]:
        result = await db.execute(
            select(Embedding)
            .where(Embedding.global_id == global_id)
            .order_by(Embedding.timestamp.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def search_similar(
        db: AsyncSession,
        query_vector: list,
        top_k: int = 10,
        threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Cosine similarity search (proposal: sub-100ms retrieval, IVFFlat/HNSW).
        Returns list of dicts with id, camera_id, track_id, global_id, confidence, timestamp, distance.
        """
        try:
            # pgvector cosine distance operator <=>; ordered by distance ascending
            qv = "[" + ",".join(str(float(x)) for x in query_vector) + "]"
            stmt = text(
                "SELECT id, camera_id, track_id, global_id, confidence, timestamp, "
                "(vector <=> :qv::vector) AS distance FROM embeddings "
                "ORDER BY vector <=> :qv::vector LIMIT :k"
            )
            result = await db.execute(stmt, {"qv": qv, "k": top_k})
            rows = result.mappings().all()
            out = [dict(r) for r in rows]
            if threshold is not None:
                # cosine distance 0 = identical; filter by max distance
                out = [r for r in out if r.get("distance") is not None and r["distance"] <= threshold]
            return out
        except Exception as e:
            logger.warning(f"pgvector search_similar failed: {e}")
            return []


# ═══════════════════════════════════════════════════════════════
# AUDIT LOG CRUD
# ═══════════════════════════════════════════════════════════════

class AuditService:
    """
    Tamper-evident audit trail operations.
    
    Each new entry is cryptographically chained to the previous one
    using SHA-256. If anyone modifies a past entry, the chain breaks.
    Metadata is encrypted with AES-256.
    """

    @staticmethod
    async def log_event(
        db: AsyncSession,
        event_type: str,
        user_id: Optional[int],
        description: str,
        metadata: Optional[Dict] = None,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        # Get the previous hash for chaining
        result = await db.execute(
            select(AuditLog.current_hash)
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
        previous_hash = result.scalar_one_or_none()

        # Build payload for hashing
        payload = {
            "event_type": event_type,
            "user_id": user_id,
            "description": description,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        current_hash = compute_hash(payload, previous_hash)

        # Encrypt metadata if present
        encrypted_meta = None
        if metadata:
            encrypted_meta = encrypt_data(metadata)

        entry = AuditLog(
            event_type=event_type,
            user_id=user_id,
            description=description,
            current_hash=current_hash,
            previous_hash=previous_hash,
            encrypted_metadata=encrypted_meta,
            ip_address=ip_address,
        )
        db.add(entry)
        await db.flush()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def get_logs(
        db: AsyncSession,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[AuditLog]:
        query = select(AuditLog).order_by(AuditLog.timestamp.desc())
        if event_type:
            query = query.where(AuditLog.event_type == event_type)
        query = query.limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def verify_integrity(db: AsyncSession) -> Dict[str, Any]:
        """Verify the entire hash chain is intact."""
        result = await db.execute(
            select(AuditLog).order_by(AuditLog.id.asc())
        )
        entries = result.scalars().all()

        for i, entry in enumerate(entries):
            expected_prev = entries[i - 1].current_hash if i > 0 else None
            if entry.previous_hash != expected_prev:
                return {
                    "valid": False,
                    "broken_at": entry.id,
                    "total": len(entries),
                    "message": f"Chain broken at entry #{entry.id}",
                }

        return {"valid": True, "broken_at": None, "total": len(entries)}


# ═══════════════════════════════════════════════════════════════
# ANALYTICS CRUD
# ═══════════════════════════════════════════════════════════════

class AnalyticsService:
    """Async operations for analytics data (foot traffic, demographics, vibe, etc.)."""

    @staticmethod
    async def save_foot_traffic(
        db: AsyncSession,
        camera_id: int,
        zone: str,
        person_count: int,
        direction_in: int = 0,
        direction_out: int = 0,
        avg_dwell_time: float = 0.0,
    ) -> FootTraffic:
        entry = FootTraffic(
            camera_id=camera_id,
            zone=zone,
            person_count=person_count,
            direction_in=direction_in,
            direction_out=direction_out,
            avg_dwell_time=avg_dwell_time,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def save_vibe_score(
        db: AsyncSession,
        overall_score: float,
        sentiment_score: float,
        energy_score: float,
        engagement_score: float,
        foot_traffic_score: float,
        label: str,
        breakdown: Optional[Dict[str, Any]] = None,
    ) -> StoreVibeScore:
        entry = StoreVibeScore(
            overall_score=float(overall_score),
            sentiment_score=float(sentiment_score),
            energy_score=float(energy_score),
            engagement_score=float(engagement_score),
            foot_traffic_score=float(foot_traffic_score),
            vibe_label=label,
            breakdown=breakdown,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def save_demographics(
        db: AsyncSession,
        camera_id: int,
        zone: Optional[str],
        age_group: Optional[str] = None,
        gender: Optional[str] = None,
        count: int = 1,
        estimated_age: Optional[float] = None,
        estimated_gender: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> DemographicSnapshot:
        entry = DemographicSnapshot(
            camera_id=camera_id,
            zone=zone,
            age_group=age_group,
            gender=gender or estimated_gender,
            estimated_age=estimated_age,
            estimated_gender=estimated_gender or gender,
            count=count,
            confidence=confidence,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def get_latest_zone_counts(
        db: AsyncSession,
        within_minutes: int = 60,
    ) -> List[Dict]:
        """
        Latest FootTraffic row per (camera_id, zone) within the lookback window.
        Used to surface the most recent stored crowd state when the live pipeline
        snapshot is empty (e.g. server just restarted but DB has history).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, within_minutes))
        # Sub-query: most recent timestamp per (camera_id, zone)
        latest = (
            select(
                FootTraffic.camera_id.label("camera_id"),
                FootTraffic.zone.label("zone"),
                func.max(FootTraffic.timestamp).label("ts"),
            )
            .where(FootTraffic.timestamp >= cutoff)
            .group_by(FootTraffic.camera_id, FootTraffic.zone)
            .subquery()
        )
        query = (
            select(FootTraffic)
            .join(
                latest,
                and_(
                    FootTraffic.camera_id == latest.c.camera_id,
                    FootTraffic.zone == latest.c.zone,
                    FootTraffic.timestamp == latest.c.ts,
                ),
            )
        )
        result = await db.execute(query)
        rows = result.scalars().all()
        return [
            {
                "camera_id": r.camera_id,
                "zone": r.zone,
                "person_count": int(r.person_count or 0),
                "avg_dwell_time": float(r.avg_dwell_time or 0.0),
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]

    @staticmethod
    async def get_hourly_traffic(
        db: AsyncSession,
        zone: Optional[str] = None,
        date: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get hourly foot traffic data for peak hours analysis."""
        target_date = date or datetime.now(timezone.utc)
        start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        query = select(
            func.extract('hour', FootTraffic.timestamp).label('hour'),
            func.sum(FootTraffic.person_count).label('total'),
            func.avg(FootTraffic.avg_dwell_time).label('avg_dwell'),
        ).where(
            and_(FootTraffic.timestamp >= start, FootTraffic.timestamp < end)
        )
        if zone:
            query = query.where(FootTraffic.zone == zone)
        query = query.group_by('hour').order_by('hour')

        result = await db.execute(query)
        return [
            {
                "hour": int(row.hour),
                "count": int(row.total or 0),
                "avg_dwell_time": float(row.avg_dwell or 0.0),
            }
            for row in result.all()
        ]

    @staticmethod
    async def get_busiest_zone_for_hour(
        db: AsyncSession,
        hour: int,
        date: Optional[datetime] = None,
    ) -> Optional[str]:
        target_date = date or datetime.now(timezone.utc)
        start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        query = (
            select(FootTraffic.zone, func.sum(FootTraffic.person_count).label("total"))
            .where(and_(FootTraffic.timestamp >= start, FootTraffic.timestamp < end))
            .where(func.extract('hour', FootTraffic.timestamp) == hour)
            .group_by(FootTraffic.zone)
            .order_by(desc("total"))
            .limit(1)
        )
        result = await db.execute(query)
        row = result.first()
        return row[0] if row else None

    @staticmethod
    async def get_vibe_trend(
        db: AsyncSession,
        hours: int = 24,
    ) -> List[StoreVibeScore]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await db.execute(
            select(StoreVibeScore)
            .where(StoreVibeScore.timestamp >= cutoff)
            .order_by(StoreVibeScore.timestamp.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def save_journey_leg(
        db: AsyncSession,
        global_id: str,
        camera_id: int,
        zone: str,
        entry_time: datetime,
        dwell_time: float,
    ) -> CustomerJourney:
        entry = CustomerJourney(
            global_id=global_id,
            camera_id=camera_id,
            zone=zone,
            entry_time=entry_time,
            dwell_time=dwell_time,
        )
        db.add(entry)
        await db.flush()
        return entry

    # Back-compat alias
    save_journey = save_journey_leg

    @staticmethod
    async def get_journey(
        db: AsyncSession,
        global_id: str,
    ) -> List[CustomerJourney]:
        result = await db.execute(
            select(CustomerJourney)
            .where(CustomerJourney.global_id == global_id)
            .order_by(CustomerJourney.entry_time.asc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_demographics_breakdown(
        db: AsyncSession,
        hours: int = 24,
        zone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate demographic snapshots across the given window into
        age/gender histograms for the current dashboard.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
        query = (
            select(
                DemographicSnapshot.age_group,
                DemographicSnapshot.gender,
                func.sum(DemographicSnapshot.count).label("total"),
            )
            .where(DemographicSnapshot.timestamp >= cutoff)
        )
        if zone:
            query = query.where(DemographicSnapshot.zone == zone)
        query = query.group_by(DemographicSnapshot.age_group, DemographicSnapshot.gender)
        result = await db.execute(query)
        rows = result.all()
        age_dist: Dict[str, int] = {}
        gender_dist: Dict[str, int] = {}
        total = 0
        for row in rows:
            n = int(row.total or 0)
            total += n
            if row.age_group:
                age_dist[row.age_group] = age_dist.get(row.age_group, 0) + n
            if row.gender:
                gender_dist[str(row.gender).lower()] = (
                    gender_dist.get(str(row.gender).lower(), 0) + n
                )
        return {
            "age_distribution": age_dist,
            "gender_distribution": gender_dist,
            "total_count": total,
            "zone": zone,
        }
