"""Alerts centre + evidence: alert_acks, evidence_items, zone_events.event_uid,
per-visit demographic columns.

Revision ID: 005_alerts_evidence
Revises: 004_reid
Create Date: 2026-11-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_alerts_evidence"
down_revision: Union[str, None] = "004_reid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEMOGRAPHIC_COLUMNS = (
    ("track_id", sa.Integer()),
    ("global_id", sa.String(length=64)),
    ("age_band", sa.String(length=16)),
    ("sample_count", sa.Integer()),
    ("visit_start", sa.DateTime(timezone=True)),
    ("visit_end", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        "alert_acks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("acknowledged_by", sa.Integer(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id", name="uq_alert_acks_source"),
        sa.CheckConstraint(
            "source IN ('zone_event', 'fire_incident', 'humanless')", name="ck_alert_acks_source"
        ),
    )

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("event_uid", sa.String(length=36), nullable=True),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('snapshot', 'clip')", name="ck_evidence_items_kind"),
    )
    op.create_index("ix_evidence_items_event_uid", "evidence_items", ["event_uid"])
    op.create_index("ix_evidence_items_source", "evidence_items", ["source", "source_id"])
    op.create_index("ix_evidence_items_camera_created", "evidence_items", ["camera_id", "created_at"])
    op.create_index("ix_evidence_items_expires_at", "evidence_items", ["expires_at"])

    op.add_column("zone_events", sa.Column("event_uid", sa.String(length=36), nullable=True))
    op.create_index("ix_zone_events_event_uid", "zone_events", ["event_uid"])

    # demographic_snapshots is range-partitioned; ADD COLUMN propagates to partitions.
    for name, type_ in _DEMOGRAPHIC_COLUMNS:
        op.add_column("demographic_snapshots", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_DEMOGRAPHIC_COLUMNS):
        op.drop_column("demographic_snapshots", name)
    op.drop_index("ix_zone_events_event_uid", table_name="zone_events")
    op.drop_column("zone_events", "event_uid")
    op.drop_index("ix_evidence_items_expires_at", table_name="evidence_items")
    op.drop_index("ix_evidence_items_camera_created", table_name="evidence_items")
    op.drop_index("ix_evidence_items_source", table_name="evidence_items")
    op.drop_index("ix_evidence_items_event_uid", table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_table("alert_acks")
