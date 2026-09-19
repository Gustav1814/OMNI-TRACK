"""
OmniTrack AI — Job persistence models
═══════════════════════════════════════════════════════════════

Mirrors VisRax's `line_passing_counts` / `roi` tables, plus a `job_runs` table
holding the configuration so a job's cards survive a backend restart.

The count tables store ONE ROW PER TRACK, carrying that track's cumulative
counters — not one row per crossing event. Totals are therefore read back with
"latest row per track, then sum", which is the same guarantee VisRax gets from
`DISTINCT ON (track_id) ... ORDER BY at_us DESC`. Summing raw rows instead
would multiply every count by the number of times it was flushed.
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
    JSON, String, Text,
)
from sqlalchemy.sql import func

from app.database import Base


class JobRun(Base):
    """A registered job: what it watches, on which camera, with which model."""

    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_camera_active", "camera_id", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)

    activity_type = Column(String(80), nullable=True, index=True)
    model = Column(String(200), nullable=True)
    tracker = Column(String(100), nullable=True)
    source = Column(Text, nullable=True)
    zone = Column(String(100), nullable=True)

    # Exactly what the Add Job modal collected.
    classes = Column(JSON, nullable=True)
    regions = Column(JSON, nullable=True)
    frame_width = Column(Integer, default=0)
    frame_height = Column(Integer, default=0)

    is_active = Column(Boolean, default=True, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LinePassingCount(Base):
    """One row per track per flush — cumulative crossing counters."""

    __tablename__ = "line_passing_counts"
    __table_args__ = (
        Index("ix_lpc_job_track_at", "job_id", "track_id", "at_us"),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)
    track_id = Column(Integer, nullable=False, index=True)

    class_name = Column(String(80), nullable=True)
    global_id = Column(String(100), nullable=True)
    region_name = Column(String(120), nullable=True, index=True)
    first_seen_in_region = Column(String(120), nullable=True)
    last_seen_in_region = Column(String(120), nullable=True)
    is_line_crossed = Column(Boolean, default=False)

    in_count = Column(Integer, default=0)
    out_count = Column(Integer, default=0)
    left_count = Column(Integer, default=0)
    right_count = Column(Integer, default=0)

    # Microseconds since epoch, matching VisRax's `at_us`.
    at_us = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RoiDwell(Base):
    """One row per track per flush — region history and dwell."""

    __tablename__ = "roi_dwell"
    __table_args__ = (
        Index("ix_roi_job_track_at", "job_id", "track_id", "at_us"),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)
    track_id = Column(Integer, nullable=False, index=True)

    class_name = Column(String(80), nullable=True)
    global_id = Column(String(100), nullable=True)
    region_name = Column(String(120), nullable=True, index=True)

    # Comma-joined, as VisRax stores them, so the visit order is preserved.
    visited_regions = Column(Text, nullable=True)
    region_dwell_us = Column(Text, nullable=True)
    current_region_dwell_us = Column(BigInteger, default=0)
    total_dwell_us = Column(BigInteger, default=0)

    at_us = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CheckoutService(Base):
    """
    One customer's visit to a checkout lane, written when they leave the box.

    Append-once-per-event, unlike LinePassingCount and RoiDwell whose rows carry
    cumulative counters rewritten on every flush. A visit is a fact that happened
    at a point in time, so it is recorded once and never revised — which is also
    what keeps this table linear in job duration rather than quadratic.
    """

    __tablename__ = "checkout_services"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)
    lane_id = Column(String(120), nullable=False, index=True)
    lane_name = Column(String(120), nullable=True)
    track_id = Column(Integer, nullable=True, index=True)
    global_id = Column(String(100), nullable=True)
    enter_at_us = Column(BigInteger, nullable=False, index=True)
    exit_at_us = Column(BigInteger, nullable=False, index=True)
    # Wait AND service; a single box cannot separate them. See checkout_analytics.
    time_in_lane_s = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CheckoutSample(Base):
    """
    Queue depth for one lane at one moment, sampled on the persistence flush.

    Needed because CheckoutService only records completed visits: without these
    samples there is no way to draw queue length over time, and no way to check
    the proposal's "queue length +/- 1 person" criterion against a manual count.
    """

    __tablename__ = "checkout_samples"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)
    lane_id = Column(String(120), nullable=False, index=True)
    lane_name = Column(String(120), nullable=True)
    queue_length = Column(Integer, nullable=False, default=0)
    wait_estimate_s = Column(Float, nullable=True)
    at_us = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JobAlert(Base):
    """
    Threshold breaches worth surfacing — currently ROI dwell overruns.
    Gives the ROI activity something that acts on its numbers rather than just
    displaying them.
    """

    __tablename__ = "job_alerts"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)
    alert_type = Column(String(60), nullable=False, index=True)
    region_name = Column(String(120), nullable=True)
    track_id = Column(Integer, nullable=True)
    class_name = Column(String(80), nullable=True)
    value = Column(Float, nullable=True)
    threshold = Column(Float, nullable=True)
    message = Column(Text, nullable=True)
    snapshot_path = Column(Text, nullable=True)
    # Zone the job ran under. Denormalised from job_runs so the alerts list can
    # filter by location without a join per row.
    zone = Column(String(100), nullable=True, index=True)
    acknowledged = Column(Boolean, default=False, nullable=False, index=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    at_us = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
