"""
OmniTrack AI — CRUD Services Layer
Async database operations for all models with proper error handling.

WHY THIS EXISTS:
  Routers should NOT talk to the database directly. This service layer
  is the middle-man: routers call services, services talk to the DB.
  This makes testing easier and keeps business logic out of HTTP handlers.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc, text
from sqlalchemy import event as sa_event
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from pathlib import Path
from loguru import logger
from app.config import settings

# Models
from app.models.user import User, UserRole
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.embedding import Embedding
from app.models.audit_log import AuditLog
from app.models.analytics import (
    FootTraffic, CustomerJourney,
    StoreVibeScore,
)

# Security
from app.security.jwt_handler import hash_password
from app.security import audit_chain
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
    async def ensure_pipeline_camera(
        db: AsyncSession,
        camera_id: int,
        source: str,
        zone: str = "default",
        camera_type: str = "pipeline",
        fps: float = 30.0,
    ) -> Camera:
        """
        Ensure a camera row exists before high-volume detection writes.

        The pipeline can be started from RTSP/file/webcam sources before an
        operator creates a camera via CRUD. This keeps FK constraints intact
        without forcing the hot path to fail.
        """
        camera = await CameraService.get_by_id(db, camera_id)
        if camera:
            changed = False
            if zone and camera.zone != zone:
                camera.zone = zone
                changed = True
            if source and camera.stream_url != source:
                camera.stream_url = source
                changed = True
            if changed:
                await db.flush()
                await db.refresh(camera)
            return camera

        camera = Camera(
            id=int(camera_id),
            name=f"Pipeline Camera {camera_id}",
            stream_url=source or f"pipeline://camera/{camera_id}",
            zone=zone,
            location=zone,
            fps=fps,
            camera_type=camera_type,
        )
        db.add(camera)
        await db.flush()
        await db.refresh(camera)
        logger.info(f"Auto-registered pipeline camera {camera_id} (zone={zone})")
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
                track_id=det.get("track_id"),
                global_id=det.get("global_id"),
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
                track_id=det.get("track_id"),
                global_id=det.get("global_id"),
                zone=det.get("zone") or zone,
            ))
        if not objects:
            return []
        db.add_all(objects)
        await db.flush()  # primary keys come back via RETURNING; no per-row refresh
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
        model_version: Optional[str] = None,
    ) -> Embedding:
        emb = Embedding(
            detection_id=detection_id,
            camera_id=camera_id,
            track_id=track_id,
            global_id=global_id,
            vector=list(embedding_vector),
            confidence=confidence,
            model_version=model_version or settings.reid_model_version,
        )
        db.add(emb)
        await db.flush()
        return emb

    @staticmethod
    async def store_batch(
        db: AsyncSession,
        rows: List[Dict[str, Any]],
        model_version: Optional[str] = None,
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
            objs.append(Embedding(
                detection_id=r.get("detection_id"),
                camera_id=int(r.get("camera_id", 0)),
                track_id=r.get("track_id"),
                global_id=r.get("global_id"),
                vector=list(vec),
                confidence=r.get("confidence"),
                model_version=model_version or settings.reid_model_version,
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
        model_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Most recent identity-tagged embeddings, newest first, for gallery warm-up.

        Only vectors from the same weights are comparable, so callers pass
        ``model_version`` (defaults to the configured Re-ID weights).
        Rows: ``{global_id, vector, timestamp (epoch s), camera_id}``.
        """
        version = model_version or settings.reid_model_version
        result = await db.execute(
            select(Embedding.global_id, Embedding.vector, Embedding.timestamp, Embedding.camera_id)
            .where(Embedding.model_version == version, Embedding.global_id.isnot(None))
            .order_by(Embedding.timestamp.desc())
            .limit(max_rows)
        )
        out = []
        for gid, vec, ts, cam in result.all():
            if vec is None:
                continue
            out.append({
                "global_id": gid,
                "vector": [float(x) for x in vec],
                "timestamp": ts.timestamp() if ts is not None else 0.0,
                "camera_id": cam,
            })
        return out

    @staticmethod
    async def get_by_global_id(
        db: AsyncSession, global_id: str, limit: int = 200
    ) -> List[Embedding]:
        result = await db.execute(
            select(Embedding)
            .where(Embedding.global_id == global_id)
            .order_by(Embedding.timestamp.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def search_similar(
        db: AsyncSession,
        query_vector: list,
        top_k: int = 10,
        threshold: Optional[float] = None,
        model_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Nearest stored embeddings by cosine similarity (HNSW, per partition).

        ``threshold`` is a minimum cosine *similarity*. Rows carry
        ``similarity`` and ``distance`` (= 1 - similarity).
        """
        qv = _vector_literal(query_vector)
        version = model_version or settings.reid_model_version
        stmt = text(
            "SELECT id, camera_id, track_id, global_id, confidence, timestamp, "
            "(vector <=> CAST(:qv AS vector)) AS distance FROM embeddings "
            "WHERE model_version = :mv "
            "ORDER BY vector <=> CAST(:qv AS vector) LIMIT :k"
        )
        result = await db.execute(stmt, {"qv": qv, "mv": version, "k": int(top_k)})
        out = []
        for r in result.mappings().all():
            row = dict(r)
            row["similarity"] = 1.0 - float(row["distance"])
            if threshold is None or row["similarity"] >= threshold:
                out.append(row)
        return out


def _vector_literal(vec) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# ═══════════════════════════════════════════════════════════════
# RE-ID IDENTITIES / TOPOLOGY / CALIBRATION
# ═══════════════════════════════════════════════════════════════

class ReIDIdentityService:
    """Identity centroids (one row per global id) kept in step with the coordinator."""

    @staticmethod
    async def upsert_batch(
        db: AsyncSession,
        identities: List[Dict[str, Any]],
        model_version: Optional[str] = None,
    ) -> int:
        """Rows: ``{global_id, centroid, n_views, first_seen, last_seen, last_camera_id}``
        with epoch-second timestamps (the coordinator's ``drain_dirty_identities``)."""
        version = model_version or settings.reid_model_version
        n = 0
        stmt = text(
            "INSERT INTO reid_identities (global_id, model_version, centroid, n_views, "
            "first_seen, last_seen, last_camera_id, updated_at) "
            "VALUES (:gid, :mv, CAST(:c AS vector), :n, :fs, :ls, "
            "(SELECT id FROM cameras WHERE id = :cam), now()) "
            "ON CONFLICT (global_id) DO UPDATE SET model_version = EXCLUDED.model_version, "
            "centroid = EXCLUDED.centroid, n_views = EXCLUDED.n_views, "
            "first_seen = LEAST(reid_identities.first_seen, EXCLUDED.first_seen), "
            "last_seen = GREATEST(reid_identities.last_seen, EXCLUDED.last_seen), "
            "last_camera_id = COALESCE(EXCLUDED.last_camera_id, reid_identities.last_camera_id), "
            "updated_at = now()"
        )
        for ident in identities:
            centroid = ident.get("centroid")
            gid = ident.get("global_id")
            if centroid is None or not gid:
                continue
            await db.execute(stmt, {
                "gid": str(gid)[:100],
                "mv": version,
                "c": _vector_literal(centroid),
                "n": int(ident.get("n_views") or 1),
                "fs": _epoch_to_dt(ident.get("first_seen")),
                "ls": _epoch_to_dt(ident.get("last_seen")),
                "cam": ident.get("last_camera_id"),
            })
            n += 1
        return n

    @staticmethod
    async def search(
        db: AsyncSession,
        query_vector: list,
        top_k: int = 10,
        threshold: Optional[float] = None,
        model_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Nearest identities by centroid cosine similarity (HNSW)."""
        stmt = text(
            "SELECT global_id, n_views, first_seen, last_seen, last_camera_id, "
            "(centroid <=> CAST(:qv AS vector)) AS distance FROM reid_identities "
            "WHERE model_version = :mv ORDER BY centroid <=> CAST(:qv AS vector) LIMIT :k"
        )
        result = await db.execute(stmt, {
            "qv": _vector_literal(query_vector),
            "mv": model_version or settings.reid_model_version,
            "k": int(top_k),
        })
        out = []
        for r in result.mappings().all():
            row = dict(r)
            row["similarity"] = 1.0 - float(row["distance"])
            if threshold is None or row["similarity"] >= threshold:
                out.append(row)
        return out

    @staticmethod
    async def list_recent(
        db: AsyncSession, limit: int = 100, model_version: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                "SELECT global_id, n_views, first_seen, last_seen, last_camera_id "
                "FROM reid_identities WHERE model_version = :mv "
                "ORDER BY last_seen DESC LIMIT :k"
            ),
            {"mv": model_version or settings.reid_model_version, "k": int(limit)},
        )
        return [dict(r) for r in result.mappings().all()]

    @staticmethod
    async def count(db: AsyncSession, model_version: Optional[str] = None) -> int:
        result = await db.execute(
            text("SELECT count(*) FROM reid_identities WHERE model_version = :mv"),
            {"mv": model_version or settings.reid_model_version},
        )
        return int(result.scalar() or 0)


def _epoch_to_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


class ReIDConfigService:
    """Camera topology links and floor-plane calibrations."""

    @staticmethod
    async def list_links(db: AsyncSession) -> List[Dict[str, Any]]:
        """Links in the shape ``track_coordinator.parse_links`` accepts (directed rows)."""
        from app.models.embedding import CameraLink

        result = await db.execute(select(CameraLink).order_by(CameraLink.id))
        return [
            {
                "from": link.from_camera_id,
                "to": link.to_camera_id,
                "min_s": float(link.min_s or 0.0),
                "max_s": float(link.max_s),
                "overlapping": bool(link.overlapping),
                "bidirectional": False,
            }
            for link in result.scalars().all()
        ]

    @staticmethod
    async def replace_links(db: AsyncSession, links: List[Dict[str, Any]]) -> int:
        """Replace the whole topology. ``bidirectional`` links are stored as two rows."""
        from sqlalchemy import delete as sa_delete
        from app.models.embedding import CameraLink

        rows: Dict[tuple, CameraLink] = {}
        for link in links:
            a, b = int(link["from"]), int(link["to"])
            if a == b:
                raise ValueError("a camera cannot link to itself")
            min_s = float(link.get("min_s") or 0.0)
            max_s = float(link["max_s"])
            if min_s < 0 or max_s < min_s:
                raise ValueError(f"invalid transit window {min_s}..{max_s} for {a}->{b}")
            pairs = [(a, b), (b, a)] if link.get("bidirectional") else [(a, b)]
            for f, t in pairs:
                rows[(f, t)] = CameraLink(
                    from_camera_id=f, to_camera_id=t, min_s=min_s, max_s=max_s,
                    overlapping=bool(link.get("overlapping")),
                )
        await db.execute(sa_delete(CameraLink))
        db.add_all(list(rows.values()))
        await db.flush()
        return len(rows)

    @staticmethod
    async def list_calibrations(db: AsyncSession) -> List[Dict[str, Any]]:
        from app.models.embedding import CameraCalibration

        result = await db.execute(select(CameraCalibration))
        return [
            {"camera_id": c.camera_id, "homography": c.homography, "points": c.points, "rmse_m": c.rmse_m,
             "updated_at": c.updated_at.isoformat() if c.updated_at else None}
            for c in result.scalars().all()
        ]

    @staticmethod
    async def set_calibration(
        db: AsyncSession, camera_id: int, homography: List[List[float]],
        points: Optional[List[Dict[str, Any]]] = None, rmse_m: Optional[float] = None,
    ) -> None:
        from app.models.embedding import CameraCalibration

        row = await db.get(CameraCalibration, camera_id)
        if row is None:
            db.add(CameraCalibration(camera_id=camera_id, homography=homography, points=points, rmse_m=rmse_m))
        else:
            row.homography = homography
            row.points = points
            row.rmse_m = rmse_m
            row.updated_at = datetime.now(timezone.utc)
        await db.flush()

    @staticmethod
    async def delete_calibration(db: AsyncSession, camera_id: int) -> bool:
        from app.models.embedding import CameraCalibration

        row = await db.get(CameraCalibration, camera_id)
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True


# ═══════════════════════════════════════════════════════════════
# AUDIT LOG CRUD
# ═══════════════════════════════════════════════════════════════

class AuditService:
    """
    Tamper-evident audit trail (see app/security/audit_chain.py).

    Each entry is HMAC-SHA256-chained to the previous one with a server-held
    key (AUDIT_HMAC_KEY); metadata is AES-256-GCM encrypted before hashing.
    Editing, deleting or reordering any row breaks ``verify_integrity``;
    deleting the newest rows is caught by the signed anchor file.
    """

    @staticmethod
    async def log_event(
        db: AsyncSession,
        event_type: str,
        user_id: Optional[int],
        description: str,
        metadata: Optional[Dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        # Serialize chain extension per transaction. Without this, concurrent
        # writers can read the same previous_hash and create an invalid fork.
        if settings.DATABASE_URL.startswith("postgresql"):
            await db.execute(text("SELECT pg_advisory_xact_lock(873245)"))

        result = await db.execute(
            select(AuditLog.current_hash).order_by(AuditLog.id.desc()).limit(1)
        )
        previous_hash = result.scalar_one_or_none()
        event_ts = datetime.now(timezone.utc)
        encrypted_meta = encrypt_data(metadata) if metadata else None
        user_agent = (user_agent or None) and user_agent[:300]
        ip_address = (ip_address or None) and ip_address[:45]

        current_hash = audit_chain.compute_hmac(
            audit_chain.canonical_payload(
                event_type, user_id, description, event_ts,
                encrypted_meta, ip_address, user_agent,
            ),
            previous_hash,
            audit_chain.audit_key_bytes(),
        )
        entry = AuditLog(
            event_type=event_type,
            user_id=user_id,
            description=description,
            current_hash=current_hash,
            previous_hash=previous_hash,
            encrypted_metadata=encrypted_meta,
            ip_address=ip_address,
            user_agent=user_agent,
            timestamp=event_ts,
            hash_alg=audit_chain.HMAC_ALG,
        )
        db.add(entry)
        await db.flush()
        AuditService._anchor_after_commit(db, entry.id, current_hash)
        return entry

    @staticmethod
    def _anchor_after_commit(db: AsyncSession, entry_id: int, entry_hash: str) -> None:
        """Advance the signed anchor only once the row is durably committed."""
        sync_session = db.sync_session
        pending = sync_session.info.setdefault("audit_anchor_pending", [])
        pending.append((entry_id, entry_hash))
        if sync_session.info.get("audit_anchor_hooked"):
            return
        sync_session.info["audit_anchor_hooked"] = True

        def _after_commit(session) -> None:
            items = session.info.pop("audit_anchor_pending", [])
            if not items:
                return
            last_id, last_hash = max(items)
            try:
                audit_chain.advance_anchor(
                    Path(settings.AUDIT_ANCHOR_PATH), last_id, last_hash, audit_chain.audit_key_bytes()
                )
            except Exception as e:  # anchor is defence in depth; never fail the request
                logger.error(f"Audit anchor update failed: {e}")

        def _after_rollback(session) -> None:
            session.info.pop("audit_anchor_pending", None)

        sa_event.listen(sync_session, "after_commit", _after_commit)
        sa_event.listen(sync_session, "after_soft_rollback", lambda s, prev: _after_rollback(s))

    @staticmethod
    async def get_logs(
        db: AsyncSession,
        limit: int = 100,
        event_type: Optional[str] = None,
        offset: int = 0,
    ) -> List[AuditLog]:
        query = select(AuditLog).order_by(AuditLog.id.desc())
        if event_type:
            query = query.where(AuditLog.event_type == event_type)
        result = await db.execute(query.offset(offset).limit(limit))
        return result.scalars().all()

    @staticmethod
    async def load_chain(db: AsyncSession) -> List["audit_chain.ChainEntry"]:
        rows = await db.stream(
            select(AuditLog).order_by(AuditLog.id.asc()).execution_options(yield_per=2000)
        )
        entries: List[audit_chain.ChainEntry] = []
        async for row in rows.scalars():
            entries.append(audit_chain.ChainEntry.from_row(row))
        return entries

    @staticmethod
    async def verify_integrity(db: AsyncSession) -> Dict[str, Any]:
        """Verify the entire chain plus the signed tail anchor."""
        key = audit_chain.audit_key_bytes()
        entries = await AuditService.load_chain(db)
        anchor = audit_chain.read_anchor(Path(settings.AUDIT_ANCHOR_PATH), key)
        result = audit_chain.verify_entries(entries, key, anchor)
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        result["message"] = (
            (f"Chain intact ({result['hmac_entries']} keyed entries"
             + (f"; {result['excluded_legacy']} pre-HMAC legacy rows excluded by signed base"
                if result.get("excluded_legacy") else "")
             + ")") if result["valid"]
            else f"Chain broken at entry #{result['broken_at']}: {result['reason']}"
        )
        return result


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

