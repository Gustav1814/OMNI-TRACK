"""Zone analytics: zones, zone_events, zone_samples, track_visits,
fire_incidents, emotion_aggregates.

Revision ID: 004_zones
Revises: 004_security (linearised; was 003_store_profile, which left two heads)
Create Date: 2026-11-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_zones"
down_revision: Union[str, None] = "004_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("zone_type", sa.String(length=20), nullable=False),
        sa.Column("points", sa.JSON(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("lane", sa.String(length=50), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("camera_id", "name", name="uq_zones_camera_name"),
    )
    op.create_index("ix_zones_id", "zones", ["id"])
    op.create_index("ix_zones_camera_id", "zones", ["camera_id"])
    op.create_index("ix_zones_zone_type", "zones", ["zone_type"])

    op.create_table(
        "zone_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=True),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("global_id", sa.String(length=64), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_zone_events_type_ts", "zone_events", ["event_type", "ts"])
    op.create_index("ix_zone_events_zone_type_ts", "zone_events", ["zone_id", "event_type", "ts"])
    op.create_index("ix_zone_events_camera_ts", "zone_events", ["camera_id", "ts"])

    op.create_table(
        "zone_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Float(), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("occupancy_pct", sa.Float(), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_zone_samples_zone_ts", "zone_samples", ["zone_id", "ts"])
    op.create_index("ix_zone_samples_camera_ts", "zone_samples", ["camera_id", "ts"])

    op.create_table(
        "track_visits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=64), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_track_visits_first_seen", "track_visits", ["first_seen"])
    op.create_index("ix_track_visits_camera_first_seen", "track_visits", ["camera_id", "first_seen"])

    op.create_table(
        "fire_incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_key", sa.String(length=64), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("peak_conf", sa.Float(), nullable=False, server_default="0"),
        sa.Column("class_name", sa.String(length=32), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("frame_wh", sa.JSON(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retriggers", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_key"),
    )
    op.create_index("ix_fire_incidents_camera_id", "fire_incidents", ["camera_id"])
    op.create_index("ix_fire_incidents_started_at", "fire_incidents", ["started_at"])

    op.create_table(
        "emotion_aggregates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_s", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column("dominant", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emotion_aggregates_bucket", "emotion_aggregates", ["bucket_start"])
    op.create_index(
        "ix_emotion_aggregates_camera_zone_bucket",
        "emotion_aggregates",
        ["camera_id", "zone_id", "bucket_start"],
    )


def downgrade() -> None:
    op.drop_table("emotion_aggregates")
    op.drop_table("fire_incidents")
    op.drop_table("track_visits")
    op.drop_table("zone_samples")
    op.drop_table("zone_events")
    op.drop_table("zones")
