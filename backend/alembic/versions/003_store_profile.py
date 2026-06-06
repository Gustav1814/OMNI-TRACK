"""Store setup profile.

Revision ID: 003_store_profile
Revises: 002_humanless_store
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_store_profile"
down_revision: Union[str, None] = "002_humanless_store"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_name", sa.String(length=160), nullable=False, server_default="OmniTrack Store"),
        sa.Column("deployment_type", sa.String(length=80), nullable=False, server_default="custom"),
        sa.Column("enabled_modules", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("zone_templates", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_store_profiles_id", "store_profiles", ["id"])
    op.create_index("ix_store_profiles_deployment_type", "store_profiles", ["deployment_type"])


def downgrade() -> None:
    op.drop_table("store_profiles")
