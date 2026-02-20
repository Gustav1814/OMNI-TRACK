"""
OmniTrack AI — Embedding Model (pgvector)
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(Integer, primary_key=True, index=True)
    detection_id = Column(Integer, ForeignKey("detections.id"), nullable=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=True, index=True)
    track_id = Column(Integer, nullable=True, index=True)
    global_id = Column(String(100), nullable=True, index=True)  # Cross-camera global identity
    # pgvector column — stored as array, cast to vector in queries
    vector = Column(String, nullable=False)  # JSON-serialized 512-d vector
    vector_dim = Column(Integer, default=512)
    model_version = Column(String(50), default="osnet_x1_0")
    confidence = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
