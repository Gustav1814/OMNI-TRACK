"""Re-ID: identity centroids (HNSW), camera topology links, camera calibrations.

Revision ID: 004_reid
Revises: 004_zones (the chain is 003_store_profile -> 004_security -> 004_zones)
Create Date: 2026-11-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_reid"
down_revision: Union[str, None] = "004_zones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 512


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "reid_identities",
        sa.Column("global_id", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("centroid", sa.Text(), nullable=False),  # retyped to vector below
        sa.Column("n_views", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_camera_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["last_camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("global_id"),
    )
    op.execute(
        f"ALTER TABLE reid_identities ALTER COLUMN centroid TYPE vector({EMBEDDING_DIM}) "
        f"USING centroid::vector({EMBEDDING_DIM})"
    )
    op.create_index("ix_reid_identities_last_seen", "reid_identities", ["last_seen"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reid_identities_centroid_hnsw ON reid_identities "
        "USING hnsw (centroid vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "camera_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_camera_id", sa.Integer(), nullable=False),
        sa.Column("to_camera_id", sa.Integer(), nullable=False),
        sa.Column("min_s", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_s", sa.Float(), nullable=False),
        sa.Column("overlapping", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["from_camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_camera_id", "to_camera_id", name="uq_camera_links_pair"),
        sa.CheckConstraint("max_s >= min_s AND min_s >= 0", name="ck_camera_links_window"),
    )

    op.create_table(
        "camera_calibrations",
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("homography", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("camera_id"),
    )


def downgrade() -> None:
    op.drop_table("camera_calibrations")
    op.drop_table("camera_links")
    op.execute("DROP INDEX IF EXISTS ix_reid_identities_centroid_hnsw")
    op.drop_index("ix_reid_identities_last_seen", table_name="reid_identities")
    op.drop_table("reid_identities")
