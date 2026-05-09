"""
OmniTrack AI — Embedding Model (pgvector)
Proposal: 128-d or 512-d embeddings, cosine similarity, sub-100ms retrieval (IVFFlat/HNSW).
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
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

    id = Column(Integer, primary_key=True, index=True)
    detection_id = Column(Integer, ForeignKey("detections.id"), nullable=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=True, index=True)
    track_id = Column(Integer, nullable=True, index=True)
    global_id = Column(String(100), nullable=True, index=True)  # Cross-camera global identity
    from app.config import settings
    vector = (
        Column(Vector(EMBEDDING_DIM), nullable=False)
        if _PGVECTOR_AVAILABLE and not settings.DATABASE_URL.startswith("sqlite")
        else Column(String, nullable=False)
    )
    vector_dim = Column(Integer, default=EMBEDDING_DIM)
    model_version = Column(String(50), default="osnet_x1_0")
    confidence = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
