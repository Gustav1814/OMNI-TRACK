"""Humanless store foundation - sessions, products, virtual carts.

Revision ID: 002_humanless_store
Revises: 001_enterprise_schema
Create Date: 2026-06-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002_humanless_store"
down_revision: Union[str, None] = "001_enterprise_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(length=30), nullable=False, server_default="item"),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("product_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_index("ix_products_id", "products", ["id"])
    op.create_index("ix_products_sku", "products", ["sku"], unique=True)
    op.create_index("ix_products_name", "products", ["name"])
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_is_active", "products", ["is_active"])
    op.create_index("ix_products_active_category", "products", ["is_active", "category"])

    op.create_table(
        "shelf_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("current_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("zone_id", name="uq_shelf_zones_zone_id"),
    )
    op.create_index("ix_shelf_zones_id", "shelf_zones", ["id"])
    op.create_index("ix_shelf_zones_camera_id", "shelf_zones", ["camera_id"])
    op.create_index("ix_shelf_zones_product_id", "shelf_zones", ["product_id"])
    op.create_index("ix_shelf_zones_is_active", "shelf_zones", ["is_active"])
    op.create_index("ix_shelf_zones_camera_active", "shelf_zones", ["camera_id", "is_active"])
    op.create_index("ix_shelf_zones_product_active", "shelf_zones", ["product_id", "is_active"])

    op.create_table(
        "store_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("entry_time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("entry_camera_id", sa.Integer(), nullable=True),
        sa.Column("last_camera_id", sa.Integer(), nullable=True),
        sa.Column("entry_zone", sa.String(length=100), nullable=True),
        sa.Column("last_zone", sa.String(length=100), nullable=True),
        sa.Column("checkout_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("payment_status", sa.String(length=30), nullable=False, server_default="not_started"),
        sa.Column("session_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["entry_camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_store_sessions_id", "store_sessions", ["id"])
    op.create_index("ix_store_sessions_global_id", "store_sessions", ["global_id"])
    op.create_index("ix_store_sessions_status", "store_sessions", ["status"])
    op.create_index("ix_store_sessions_last_seen", "store_sessions", ["last_seen"])
    op.create_index("ix_store_sessions_status_seen", "store_sessions", ["status", "last_seen"])
    op.create_index("ix_store_sessions_global_status", "store_sessions", ["global_id", "status"])

    op.create_table(
        "virtual_carts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="open"),
        sa.Column("subtotal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["store_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_virtual_carts_session_id"),
    )
    op.create_index("ix_virtual_carts_id", "virtual_carts", ["id"])
    op.create_index("ix_virtual_carts_session_id", "virtual_carts", ["session_id"])
    op.create_index("ix_virtual_carts_status", "virtual_carts", ["status"])
    op.create_index("ix_virtual_carts_updated_at", "virtual_carts", ["updated_at"])
    op.create_index("ix_virtual_carts_status_updated", "virtual_carts", ["status", "updated_at"])

    op.create_table(
        "cart_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cart_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("last_event_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["cart_id"], ["virtual_carts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cart_id", "product_id", name="uq_cart_items_cart_product"),
    )
    op.create_index("ix_cart_items_id", "cart_items", ["id"])
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])
    op.create_index("ix_cart_items_product_id", "cart_items", ["product_id"])

    op.create_table(
        "cart_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cart_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("shelf_zone_id", sa.Integer(), nullable=True),
        sa.Column("camera_id", sa.Integer(), nullable=True),
        sa.Column("global_id", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("rule_source", sa.String(length=60), nullable=False, server_default="manual"),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cart_id"], ["virtual_carts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["store_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shelf_zone_id"], ["shelf_zones.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cart_events_id", "cart_events", ["id"])
    op.create_index("ix_cart_events_cart_id", "cart_events", ["cart_id"])
    op.create_index("ix_cart_events_session_id", "cart_events", ["session_id"])
    op.create_index("ix_cart_events_product_id", "cart_events", ["product_id"])
    op.create_index("ix_cart_events_global_id", "cart_events", ["global_id"])
    op.create_index("ix_cart_events_event_type", "cart_events", ["event_type"])
    op.create_index("ix_cart_events_timestamp", "cart_events", ["timestamp"])
    op.create_index("ix_cart_events_session_time", "cart_events", ["session_id", "timestamp"])
    op.create_index("ix_cart_events_cart_time", "cart_events", ["cart_id", "timestamp"])
    op.create_index("ix_cart_events_product_time", "cart_events", ["product_id", "timestamp"])
    op.create_index("ix_cart_events_type_time", "cart_events", ["event_type", "timestamp"])

    op.create_table(
        "loss_prevention_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("cart_id", sa.Integer(), nullable=True),
        sa.Column("alert_type", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="open"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cart_id"], ["virtual_carts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["store_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_loss_prevention_alerts_id", "loss_prevention_alerts", ["id"])
    op.create_index("ix_loss_prevention_alerts_session_id", "loss_prevention_alerts", ["session_id"])
    op.create_index("ix_loss_prevention_alerts_alert_type", "loss_prevention_alerts", ["alert_type"])
    op.create_index("ix_loss_prevention_alerts_status", "loss_prevention_alerts", ["status"])
    op.create_index("ix_loss_prevention_alerts_timestamp", "loss_prevention_alerts", ["timestamp"])
    op.create_index("ix_loss_alerts_status_time", "loss_prevention_alerts", ["status", "timestamp"])
    op.create_index("ix_loss_alerts_session_time", "loss_prevention_alerts", ["session_id", "timestamp"])


def downgrade() -> None:
    op.drop_table("loss_prevention_alerts")
    op.drop_table("cart_events")
    op.drop_table("cart_items")
    op.drop_table("virtual_carts")
    op.drop_table("store_sessions")
    op.drop_table("shelf_zones")
    op.drop_table("products")
