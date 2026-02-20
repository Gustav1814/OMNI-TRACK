"""
OmniTrack AI — Analytics Models
Extended analytics storage for store vibe, heatmaps, demographics, etc.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class FootTraffic(Base):
    """Stores foot traffic heatmap data per zone per time interval."""
    __tablename__ = "foot_traffic"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    zone = Column(String(100), nullable=False, index=True)
    person_count = Column(Integer, default=0)
    avg_dwell_time = Column(Float, default=0.0)  # seconds
    heatmap_data = Column(JSON, nullable=True)  # Grid-based heat intensity
    interval_start = Column(DateTime(timezone=True), nullable=False, index=True)
    interval_end = Column(DateTime(timezone=True), nullable=False)


class CustomerJourney(Base):
    """Tracks a customer's path through the store via Re-ID."""
    __tablename__ = "customer_journeys"

    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String(100), nullable=False, index=True)
    journey_data = Column(JSON, nullable=False)  # [{camera_id, zone, timestamp, duration}]
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    total_duration = Column(Float, nullable=True)  # seconds
    zones_visited = Column(Integer, default=0)
    date = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class DemographicSnapshot(Base):
    """Age/gender demographics estimated via DeepFace."""
    __tablename__ = "demographic_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    zone = Column(String(100), nullable=True, index=True)
    estimated_age = Column(Float, nullable=True)
    estimated_gender = Column(String(20), nullable=True)
    confidence = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class StoreVibeScore(Base):
    """Aggregated 'vibe' score combining sentiment, crowd energy, engagement."""
    __tablename__ = "store_vibe_scores"

    id = Column(Integer, primary_key=True, index=True)
    overall_score = Column(Float, nullable=False)  # 0-100
    sentiment_score = Column(Float, default=50.0)  # From emotion recognition
    energy_score = Column(Float, default=50.0)  # From crowd density / movement
    engagement_score = Column(Float, default=50.0)  # From shelf dwell time
    foot_traffic_score = Column(Float, default=50.0)  # From traffic volume
    breakdown = Column(JSON, nullable=True)  # Per-zone scores
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class PeakHoursData(Base):
    """Peak hours and traffic patterns."""
    __tablename__ = "peak_hours"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    hour = Column(Integer, nullable=False)
    visitor_count = Column(Integer, default=0)
    avg_dwell_time = Column(Float, default=0.0)
    busiest_zone = Column(String(100), nullable=True)
    zone_breakdown = Column(JSON, nullable=True)
