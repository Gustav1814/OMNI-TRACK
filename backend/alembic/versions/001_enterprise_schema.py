"""Enterprise initial schema — pgvector, HNSW, composite indexes, FK cascades.

Revision ID: 001_enterprise_schema
Revises:
Create Date: 2026-05-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_enterprise_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

userrole = postgresql.ENUM("admin", "operator", "viewer", name="userrole", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    userrole.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=True),
        sa.Column("role", userrole, nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_role_active", "users", ["role", "is_active"], unique=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("stream_url", sa.String(length=500), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("zone", sa.String(length=100), nullable=True),
        sa.Column("resolution_w", sa.Integer(), nullable=True, server_default="1920"),
        sa.Column("resolution_h", sa.Integer(), nullable=True, server_default="1080"),
        sa.Column("fps", sa.Float(), nullable=True, server_default="30"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
        sa.Column("camera_type", sa.String(length=50), nullable=True, server_default="general"),
        sa.Column("roi_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cameras_id", "cameras", ["id"], unique=False)
    op.create_index("ix_cameras_type_active", "cameras", ["camera_type", "is_active"], unique=False)
    op.create_index("ix_cameras_zone", "cameras", ["zone"], unique=False)
    op.create_index("ix_cameras_zone_active", "cameras", ["zone", "is_active"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("encrypted_metadata", sa.Text(), nullable=True),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_hash"),
    )
    op.create_index("ix_audit_logs_event_timestamp", "audit_logs", ["event_type", "timestamp"], unique=False)
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"], unique=False)
    op.create_index("ix_audit_logs_id", "audit_logs", ["id"], unique=False)
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"], unique=False)
    op.create_index("ix_audit_logs_user_timestamp", "audit_logs", ["user_id", "timestamp"], unique=False)

    op.execute(
        """
        CREATE TABLE detections (
            id SERIAL,
            camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            timestamp TIMESTAMPTZ DEFAULT now(),
            track_id INTEGER,
            global_id VARCHAR(100),
            bbox_x FLOAT NOT NULL,
            bbox_y FLOAT NOT NULL,
            bbox_w FLOAT NOT NULL,
            bbox_h FLOAT NOT NULL,
            confidence FLOAT NOT NULL,
            class_name VARCHAR(50) DEFAULT 'person',
            zone VARCHAR(100),
            metadata JSON
        ) PARTITION BY RANGE (timestamp)
        """
    )
    op.execute("CREATE TABLE detections_default PARTITION OF detections DEFAULT")
    op.create_index("ix_detections_camera_id", "detections", ["camera_id"], unique=False)
    op.create_index("ix_detections_camera_timestamp", "detections", ["camera_id", "timestamp"], unique=False)
    op.create_index("ix_detections_global_id", "detections", ["global_id"], unique=False)
    op.create_index("ix_detections_global_id_timestamp", "detections", ["global_id", "timestamp"], unique=False)
    op.create_index("ix_detections_id", "detections", ["id"], unique=False)
    op.create_index("ix_detections_timestamp", "detections", ["timestamp"], unique=False)
    op.create_index("ix_detections_track_id", "detections", ["track_id"], unique=False)
    op.create_index("ix_detections_zone", "detections", ["zone"], unique=False)
    op.create_index("ix_detections_zone_timestamp", "detections", ["zone", "timestamp"], unique=False)

    op.execute(
        """
        CREATE TABLE embeddings (
            id SERIAL,
            detection_id INTEGER,
            camera_id INTEGER REFERENCES cameras(id) ON DELETE CASCADE,
            track_id INTEGER,
            global_id VARCHAR(100),
            vector vector(512) NOT NULL,
            vector_dim INTEGER DEFAULT 512,
            model_version VARCHAR(50) DEFAULT 'osnet_x1_0',
            confidence FLOAT,
            timestamp TIMESTAMPTZ DEFAULT now()
        ) PARTITION BY RANGE (timestamp)
        """
    )
    op.execute("CREATE TABLE embeddings_default PARTITION OF embeddings DEFAULT")

    op.create_index("ix_embeddings_camera_id", "embeddings", ["camera_id"], unique=False)
    op.create_index("ix_embeddings_camera_timestamp", "embeddings", ["camera_id", "timestamp"], unique=False)
    op.create_index("ix_embeddings_detection_id", "embeddings", ["detection_id"], unique=False)
    op.create_index("ix_embeddings_global_id", "embeddings", ["global_id"], unique=False)
    op.create_index("ix_embeddings_global_id_model", "embeddings", ["global_id", "model_version"], unique=False)
    op.create_index("ix_embeddings_global_id_timestamp", "embeddings", ["global_id", "timestamp"], unique=False)
    op.create_index("ix_embeddings_id", "embeddings", ["id"], unique=False)
    op.create_index("ix_embeddings_timestamp", "embeddings", ["timestamp"], unique=False)
    op.create_index("ix_embeddings_track_id", "embeddings", ["track_id"], unique=False)

    op.execute(
        """
        CREATE INDEX ix_embeddings_default_vector_hnsw ON embeddings_default
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    op.execute(
        """
        CREATE TABLE foot_traffic (
            id SERIAL,
            camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            zone VARCHAR(100) NOT NULL,
            person_count INTEGER DEFAULT 0,
            direction_in INTEGER DEFAULT 0,
            direction_out INTEGER DEFAULT 0,
            avg_dwell_time FLOAT DEFAULT 0,
            heatmap_data JSON,
            timestamp TIMESTAMPTZ DEFAULT now(),
            interval_start TIMESTAMPTZ,
            interval_end TIMESTAMPTZ
        ) PARTITION BY RANGE (timestamp)
        """
    )
    op.execute("CREATE TABLE foot_traffic_default PARTITION OF foot_traffic DEFAULT")
    op.create_index("ix_foot_traffic_camera_zone_ts", "foot_traffic", ["camera_id", "zone", "timestamp"], unique=False)
    op.create_index("ix_foot_traffic_id", "foot_traffic", ["id"], unique=False)
    op.create_index("ix_foot_traffic_timestamp", "foot_traffic", ["timestamp"], unique=False)
    op.create_index("ix_foot_traffic_zone", "foot_traffic", ["zone"], unique=False)

    op.create_table(
        "customer_journeys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=100), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=True),
        sa.Column("zone", sa.String(length=100), nullable=True),
        sa.Column("dwell_time", sa.Float(), nullable=True, server_default="0"),
        sa.Column("journey_data", sa.JSON(), nullable=True),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_duration", sa.Float(), nullable=True),
        sa.Column("zones_visited", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("date", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customer_journeys_camera_id", "customer_journeys", ["camera_id"], unique=False)
    op.create_index("ix_customer_journeys_camera_zone", "customer_journeys", ["camera_id", "zone"], unique=False)
    op.create_index("ix_customer_journeys_date", "customer_journeys", ["date"], unique=False)
    op.create_index("ix_customer_journeys_global_id", "customer_journeys", ["global_id"], unique=False)
    op.create_index("ix_customer_journeys_id", "customer_journeys", ["id"], unique=False)
    op.create_index("ix_journeys_global_entry", "customer_journeys", ["global_id", "entry_time"], unique=False)
    op.create_index("ix_customer_journeys_zone", "customer_journeys", ["zone"], unique=False)

    op.execute(
        """
        CREATE TABLE demographic_snapshots (
            id SERIAL,
            camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
            zone VARCHAR(100),
            estimated_age FLOAT,
            estimated_gender VARCHAR(20),
            age_group VARCHAR(20),
            gender VARCHAR(20),
            count INTEGER DEFAULT 1,
            confidence FLOAT,
            timestamp TIMESTAMPTZ DEFAULT now()
        ) PARTITION BY RANGE (timestamp)
        """
    )
    op.execute("CREATE TABLE demographic_snapshots_default PARTITION OF demographic_snapshots DEFAULT")
    op.create_index("ix_demographic_snapshots_age_group", "demographic_snapshots", ["age_group"], unique=False)
    op.create_index("ix_demographic_snapshots_camera_id", "demographic_snapshots", ["camera_id"], unique=False)
    op.create_index("ix_demographics_camera_zone_ts", "demographic_snapshots", ["camera_id", "zone", "timestamp"], unique=False)
    op.create_index("ix_demographic_snapshots_id", "demographic_snapshots", ["id"], unique=False)
    op.create_index("ix_demographic_snapshots_timestamp", "demographic_snapshots", ["timestamp"], unique=False)
    op.create_index("ix_demographic_snapshots_zone", "demographic_snapshots", ["zone"], unique=False)

    op.create_table(
        "store_vibe_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=True, server_default="50"),
        sa.Column("energy_score", sa.Float(), nullable=True, server_default="50"),
        sa.Column("engagement_score", sa.Float(), nullable=True, server_default="50"),
        sa.Column("foot_traffic_score", sa.Float(), nullable=True, server_default="50"),
        sa.Column("vibe_label", sa.String(length=30), nullable=True),
        sa.Column("breakdown", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_store_vibe_scores_id", "store_vibe_scores", ["id"], unique=False)
    op.create_index("ix_store_vibe_scores_timestamp", "store_vibe_scores", ["timestamp"], unique=False)
    op.create_index("ix_vibe_timestamp_score", "store_vibe_scores", ["timestamp", "overall_score"], unique=False)

    op.create_table(
        "peak_hours",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("visitor_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("avg_dwell_time", sa.Float(), nullable=True, server_default="0"),
        sa.Column("busiest_zone", sa.String(length=100), nullable=True),
        sa.Column("zone_breakdown", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "hour", name="uq_peak_hours_date_hour"),
    )
    op.create_index("ix_peak_hours_date", "peak_hours", ["date"], unique=False)
    op.create_index("ix_peak_hours_date_hour", "peak_hours", ["date", "hour"], unique=False)
    op.create_index("ix_peak_hours_id", "peak_hours", ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("peak_hours")
    op.drop_table("store_vibe_scores")
    op.drop_table("demographic_snapshots")
    op.drop_table("customer_journeys")
    op.drop_table("foot_traffic")
    op.execute("DROP INDEX IF EXISTS ix_embeddings_default_vector_hnsw")
    op.drop_table("embeddings")
    op.drop_table("detections")
    op.drop_table("audit_logs")
    op.drop_table("cameras")
    op.drop_table("users")
    userrole.drop(op.get_bind(), checkfirst=True)
