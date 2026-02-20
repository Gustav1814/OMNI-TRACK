"""
OmniTrack AI — Camera Model
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, JSON
from sqlalchemy.sql import func
from app.database import Base


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    stream_url = Column(String(500), nullable=False)
    location = Column(String(200), nullable=True)
    zone = Column(String(100), nullable=True, index=True)
    resolution_w = Column(Integer, default=1920)
    resolution_h = Column(Integer, default=1080)
    fps = Column(Float, default=30.0)
    is_active = Column(Boolean, default=True)
    camera_type = Column(String(50), default="general")  # general, shelf, checkout, entrance
    roi_config = Column(JSON, nullable=True)  # Region of interest zones
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
