"""Alerts centre models: acknowledgements and captured evidence (snapshot / clip)."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base

ALERT_SOURCES = ("zone_event", "fire_incident", "humanless")
EVIDENCE_KINDS = ("snapshot", "clip")


class AlertAck(Base):
    __tablename__ = "alert_acks"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_alert_acks_source"),
        CheckConstraint(
            "source IN ('zone_event', 'fire_incident', 'humanless')", name="ck_alert_acks_source"
        ),
    )

    id = Column(Integer, primary_key=True)
    source = Column(String(16), nullable=False)
    source_id = Column(Integer, nullable=False)
    acknowledged_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    note = Column(Text, nullable=True)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        Index("ix_evidence_items_event_uid", "event_uid"),
        Index("ix_evidence_items_source", "source", "source_id"),
        Index("ix_evidence_items_camera_created", "camera_id", "created_at"),
        Index("ix_evidence_items_expires_at", "expires_at"),
        CheckConstraint("kind IN ('snapshot', 'clip')", name="ck_evidence_items_kind"),
    )

    id = Column(Integer, primary_key=True)
    source = Column(String(16), nullable=False)
    source_id = Column(Integer, nullable=True)       # resolved via event_uid when the row id is unknown
    event_uid = Column(String(36), nullable=True)
    camera_id = Column(Integer, nullable=False)       # no FK: evidence outlives a camera row
    track_id = Column(Integer, nullable=True)
    kind = Column(String(16), nullable=False)
    path = Column(Text, nullable=False)                # relative to the evidence root
    encrypted = Column(Boolean, nullable=False, default=True, server_default="true")
    bytes = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
