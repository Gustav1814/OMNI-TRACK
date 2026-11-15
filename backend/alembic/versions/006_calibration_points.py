"""Floor calibration: keep the clicked image/floor point pairs and the fit error.

Revision ID: 006_calibration_points
Revises: 005_alerts_evidence
Create Date: 2026-11-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_calibration_points"
down_revision: Union[str, None] = "005_alerts_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("camera_calibrations", sa.Column("points", sa.JSON(), nullable=True))
    op.add_column("camera_calibrations", sa.Column("rmse_m", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("camera_calibrations", "rmse_m")
    op.drop_column("camera_calibrations", "points")
