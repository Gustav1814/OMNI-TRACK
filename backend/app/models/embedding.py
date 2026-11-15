"""
OmniTrack AI — Embedding Model (pgvector)
Proposal: 128-d or 512-d embeddings, cosine similarity, sub-100ms retrieval (IVFFlat/HNSW).
"""

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.sql import func
from app.database import Base

try:
    from pgvector.sqlalchemy import Vector
    _PGVECTOR_AVAILABLE = True
except ImportError:
    Vector = None  # type: ignore
    _PGVECTOR_AVAILABLE = False

# Re-ID embeddings are 512-d (e.g. OSNet); proposal allows 128-d or 512-d
EMBEDDING_DIM = 512


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index("ix_embeddings_global_id_timestamp", "global_id", "timestamp"),
        Index("ix_embeddings_camera_timestamp", "camera_id", "timestamp"),
        Index("ix_embeddings_global_id_model", "global_id", "model_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    detection_id = Column(
        Integer, ForeignKey("detections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    camera_id = Column(
        Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True, index=True
    )
    track_id = Column(Integer, nullable=True, index=True)
    global_id = Column(String(100), nullable=True, index=True)  # Cross-camera global identity
    # pgvector column for cosine similarity search (IVFFlat/HNSW in production)
    vector = (
        Column(Vector(EMBEDDING_DIM), nullable=False)
        if _PGVECTOR_AVAILABLE
        else Column(String, nullable=False)
    )
    vector_dim = Column(Integer, default=EMBEDDING_DIM)
    model_version = Column(String(50), default="osnet_x1_0")
    confidence = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ReIDIdentity(Base):
    """One row per global identity: the running centroid the coordinator matches
    against, so warm-start and search do not need to scan raw embeddings."""

    __tablename__ = "reid_identities"
    __table_args__ = (Index("ix_reid_identities_last_seen", "last_seen"),)

    global_id = Column(String(100), primary_key=True)
    model_version = Column(String(50), nullable=False)
    centroid = (
        Column(Vector(EMBEDDING_DIM), nullable=False)
        if _PGVECTOR_AVAILABLE
        else Column(String, nullable=False)
    )
    n_views = Column(Integer, nullable=False, default=1)
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)
    last_camera_id = Column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CameraLink(Base):
    """Directed camera adjacency used for Re-ID transit-time gating."""

    __tablename__ = "camera_links"
    __table_args__ = (
        UniqueConstraint("from_camera_id", "to_camera_id", name="uq_camera_links_pair"),
    )

    id = Column(Integer, primary_key=True)
    from_camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    to_camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    min_s = Column(Float, nullable=False, default=0.0)
    max_s = Column(Float, nullable=False)
    overlapping = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CameraCalibration(Base):
    """Image -> floor-plane homography (3x3, metres) for overlapping-view fusion."""

    __tablename__ = "camera_calibrations"

    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    homography = Column(JSON, nullable=False)
    points = Column(JSON, nullable=True)  # [{"image": [u, v] (0..1), "floor": [x, y] (m)}] when fitted from clicks
    rmse_m = Column(Float, nullable=True)  # floor reprojection error of the fit
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
