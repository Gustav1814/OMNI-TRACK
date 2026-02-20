"""
OmniTrack AI — Audit Log Model (SHA-256 Hash Chain)
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    description = Column(Text, nullable=True)
    encrypted_metadata = Column(Text, nullable=True)  # AES-256-CBC encrypted
    current_hash = Column(String(64), nullable=False, unique=True)
    previous_hash = Column(String(64), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(300), nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
