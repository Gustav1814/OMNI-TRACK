"""
Zone analytics models (Remediation Plan, Phase 3).

zones               polygons / entry lines drawn per camera (normalised 0..1)
zone_events         measured events: visits, passes, queue waits, services,
                    line crossings, crowd level changes and alerts
zone_samples        5 s smoothed counts per zone (zone_id NULL = whole camera)
track_visits        one row per confirmed track (first/last seen) for peak hours
fire_incidents      fire alerts (k-of-n raise, 30 s auto-clear, cooldown re-open)
emotion_aggregates  emotion label counts per camera/zone per 5-minute bucket

The legacy humanless `shelf_zones` table (bbox per product) is unrelated and
left untouched.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base

ZONE_TYPES = ("crowd", "shelf", "queue", "counter", "entry_line", "restricted")


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("camera_id", "name", name="uq_zones_camera_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    zone_type = Column(String(20), nullable=False, index=True)
    points = Column(JSON, nullable=False)          # [[x, y], ...] normalised to the frame
    capacity = Column(Integer, nullable=True)      # crowd zones: people at 100 %
    lane = Column(String(50), nullable=True)       # pairs a queue with its counter
    config = Column(JSON, nullable=True)           # entry_line: {"in_sign": 1 | -1}
    color = Column(String(20), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZoneEvent(Base):
    __tablename__ = "zone_events"
    __table_args__ = (
        Index("ix_zone_events_type_ts", "event_type", "ts"),
        Index("ix_zone_events_zone_type_ts", "zone_id", "event_type", "ts"),
        Index("ix_zone_events_camera_ts", "camera_id", "ts"),
        Index("ix_zone_events_event_uid", "event_uid"),
    )

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, nullable=True)     # no FK: history outlives a camera row
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="CASCADE"), nullable=True)
    event_type = Column(String(32), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    track_id = Column(Integer, nullable=True)
    global_id = Column(String(64), nullable=True)
    duration_s = Column(Float, nullable=True)
    value = Column(Float, nullable=True)
    payload = Column(JSON, nullable=True)
    event_uid = Column(String(36), nullable=True)  # alert events: links evidence before the row id exists


class ZoneSample(Base):
    __tablename__ = "zone_samples"
    __table_args__ = (
        Index("ix_zone_samples_zone_ts", "zone_id", "ts"),
        Index("ix_zone_samples_camera_ts", "camera_id", "ts"),
    )

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, nullable=False)
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="CASCADE"), nullable=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    count = Column(Float, nullable=False)          # median-smoothed count
    raw_count = Column(Integer, nullable=True)
    capacity = Column(Integer, nullable=True)
    occupancy_pct = Column(Float, nullable=True)   # count / capacity (crowd zones)
    level = Column(String(16), nullable=True)


class TrackVisit(Base):
    __tablename__ = "track_visits"
    __table_args__ = (
        Index("ix_track_visits_first_seen", "first_seen"),
        Index("ix_track_visits_camera_first_seen", "camera_id", "first_seen"),
    )

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, nullable=False)
    track_id = Column(Integer, nullable=False)
    global_id = Column(String(64), nullable=True)
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)
    duration_s = Column(Float, nullable=False)


class FireIncident(Base):
    __tablename__ = "fire_incidents"

    id = Column(Integer, primary_key=True)
    incident_key = Column(String(64), nullable=False, unique=True)
    camera_id = Column(Integer, nullable=False, index=True)
    status = Column(String(16), nullable=False, default="active")   # active | cleared
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_hit_at = Column(DateTime(timezone=True), nullable=True)
    cleared_at = Column(DateTime(timezone=True), nullable=True)
    peak_conf = Column(Float, nullable=False, default=0.0)
    class_name = Column(String(32), nullable=True)
    bbox = Column(JSON, nullable=True)              # [x, y, w, h] pixels of the peak hit
    frame_wh = Column(JSON, nullable=True)
    hits = Column(Integer, nullable=False, default=0)
    retriggers = Column(Integer, nullable=False, default=0)


class EmotionAggregate(Base):
    __tablename__ = "emotion_aggregates"
    __table_args__ = (
        Index("ix_emotion_aggregates_bucket", "bucket_start"),
        Index("ix_emotion_aggregates_camera_zone_bucket", "camera_id", "zone_id", "bucket_start"),
    )

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, nullable=False)
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="CASCADE"), nullable=True)
    bucket_start = Column(DateTime(timezone=True), nullable=False)
    bucket_s = Column(Integer, nullable=False, default=300)
    counts = Column(JSON, nullable=False)           # {label: n}
    total = Column(Integer, nullable=False, default=0)
    sentiment = Column(Float, nullable=True)        # mean valence in [-1, 1]
    dominant = Column(String(16), nullable=True)
