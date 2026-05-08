"""
OmniTrack AI — Detection Model
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.sql import func
from app.database import Base


class Detection(Base):
    __tablename__ = "detections"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    track_id = Column(Integer, nullable=True, index=True)
    global_id = Column(String(100), nullable=True, index=True)  # Cross-camera Re-ID (e.g. PERSON-00042)
    bbox_x = Column(Float, nullable=False)
    bbox_y = Column(Float, nullable=False)
    bbox_w = Column(Float, nullable=False)
    bbox_h = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    class_name = Column(String(50), default="person")
    zone = Column(String(100), nullable=True, index=True)
    metadata_ = Column("metadata", JSON, nullable=True)
