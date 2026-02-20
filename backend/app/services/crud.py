"""
OmniTrack AI — CRUD Services Layer
Async database operations for all models with proper error handling.

WHY THIS EXISTS:
  Routers should NOT talk to the database directly. This service layer
  is the middle-man: routers call services, services talk to the DB.
  This makes testing easier and keeps business logic out of HTTP handlers.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, and_, desc
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

class DetectionService:
    """Async operations for saving and querying detections."""

    @staticmethod
    async def create_batch(
        db: AsyncSession,
        camera_id: int,
        detections: List[Dict[str, Any]],
    ) -> int:
        """
        Batch-insert detections from a processing frame.
        Returns count of inserted rows.
        """
        objects = []
        for det in detections:
            objects.append(Detection(
                camera_id=camera_id,
                bbox_x=det.get("bbox", [0])[0] if det.get("bbox") else 0,
                bbox_y=det.get("bbox", [0, 0])[1] if det.get("bbox") else 0,
                bbox_w=det.get("bbox", [0, 0, 0])[2] if det.get("bbox") else 0,
                bbox_h=det.get("bbox", [0, 0, 0, 0])[3] if det.get("bbox") else 0,
                confidence=det.get("confidence", 0),
                class_name=det.get("class", "person"),
                track_id=det.get("track_id"),
            ))
        db.add_all(objects)
        await db.flush()
        return len(objects)

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
        detection_id: int,
        camera_id: int,
        embedding_vector: list,
        global_id: Optional[str] = None,
    ) -> Embedding:
        emb = Embedding(
            detection_id=detection_id,
            camera_id=camera_id,
            vector=embedding_vector,
            global_id=global_id,
        )
        db.add(emb)
        await db.flush()
        return emb

    @staticmethod
    async def get_by_global_id(
        db: AsyncSession, global_id: str
    ) -> List[Embedding]:
        result = await db.execute(
            select(Embedding)
            .where(Embedding.global_id == global_id)
            .order_by(Embedding.created_at.desc())
        )
        return result.scalars().all()


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
        query = select(AuditLog).order_by(AuditLog.created_at.desc())
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
    ) -> FootTraffic:
        entry = FootTraffic(
            camera_id=camera_id,
            zone=zone,
            person_count=person_count,
            direction_in=direction_in,
            direction_out=direction_out,
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
    ) -> StoreVibeScore:
        entry = StoreVibeScore(
            overall_score=overall_score,
            sentiment_score=sentiment_score,
            energy_score=energy_score,
            engagement_score=engagement_score,
            foot_traffic_score=foot_traffic_score,
            vibe_label=label,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def save_demographics(
        db: AsyncSession,
        camera_id: int,
        zone: str,
        age_group: str,
        gender: str,
        count: int = 1,
    ) -> DemographicSnapshot:
        entry = DemographicSnapshot(
            camera_id=camera_id,
            zone=zone,
            age_group=age_group,
            gender=gender,
            count=count,
        )
        db.add(entry)
        await db.flush()
        return entry

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
        ).where(
            and_(FootTraffic.timestamp >= start, FootTraffic.timestamp < end)
        )
        if zone:
            query = query.where(FootTraffic.zone == zone)
        query = query.group_by('hour').order_by('hour')

        result = await db.execute(query)
        return [{"hour": int(row.hour), "count": int(row.total)} for row in result.all()]

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
    async def save_journey(
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
