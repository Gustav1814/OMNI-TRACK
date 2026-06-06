"""
OmniTrack AI - Store setup profile.

Singleton deployment profile used by the Setup Hub. Multi-store tenancy can add
store_id later without changing the frontend contract.
"""

from sqlalchemy import Column, DateTime, Integer, JSON, String
from sqlalchemy.sql import func

from app.database import Base


class StoreProfile(Base):
    """Store deployment template, enabled modules, and default zones."""

    __tablename__ = "store_profiles"

    id = Column(Integer, primary_key=True, index=True)
    store_name = Column(String(160), nullable=False, default="OmniTrack Store")
    deployment_type = Column(String(80), nullable=False, default="custom", index=True)
    enabled_modules = Column(JSON, nullable=False, default=list)
    zone_templates = Column(JSON, nullable=False, default=list)
    setup_completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
