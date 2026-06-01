"""
OmniTrack AI - Humanless Store Models

Low-cost cashierless foundation: regular CCTV + Re-ID + shelf zones create
customer sessions and virtual carts without requiring expensive sensor walls.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base


class Product(Base):
    """Sellable item mapped to one or more shelf zones."""

    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_active_category", "is_active", "category"),
    )

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(160), nullable=False, index=True)
    category = Column(String(100), nullable=True, index=True)
    price = Column(Float, nullable=False, default=0.0)
    unit = Column(String(30), nullable=False, default="item")
    image_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    product_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ShelfZone(Base):
    """
    Camera-visible shelf area. A shelf zone can point to one default product
    for cheap deployments where planogram position is the product signal.
    """

    __tablename__ = "shelf_zones"
    __table_args__ = (
        UniqueConstraint("zone_id", name="uq_shelf_zones_zone_id"),
        Index("ix_shelf_zones_camera_active", "camera_id", "is_active"),
        Index("ix_shelf_zones_product_active", "product_id", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    zone_id = Column(String(100), nullable=False)
    name = Column(String(160), nullable=False)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    bbox = Column(JSON, nullable=True)  # [x1, y1, x2, y2] in frame pixels or normalized coords
    current_stock = Column(Integer, nullable=False, default=0)
    low_stock_threshold = Column(Integer, nullable=False, default=3)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class StoreSession(Base):
    """One customer visit, linked to the Re-ID global_id."""

    __tablename__ = "store_sessions"
    __table_args__ = (
        Index("ix_store_sessions_status_seen", "status", "last_seen"),
        Index("ix_store_sessions_global_status", "global_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String(100), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="active", index=True)
    entry_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    entry_camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    last_camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    entry_zone = Column(String(100), nullable=True)
    last_zone = Column(String(100), nullable=True)
    checkout_confidence = Column(Float, nullable=False, default=0.0)
    payment_status = Column(String(30), nullable=False, default="not_started")
    session_metadata = Column(JSON, nullable=True)


class VirtualCart(Base):
    """Open cart attached to a store session."""

    __tablename__ = "virtual_carts"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_virtual_carts_session_id"),
        Index("ix_virtual_carts_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("store_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="open", index=True)
    subtotal = Column(Float, nullable=False, default=0.0)
    item_count = Column(Integer, nullable=False, default=0)
    confidence = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)


class CartItem(Base):
    """Current quantity for a product inside a virtual cart."""

    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("cart_id", "product_id", name="uq_cart_items_cart_product"),
    )

    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("virtual_carts.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    unit_price = Column(Float, nullable=False, default=0.0)
    confidence = Column(Float, nullable=False, default=1.0)
    last_event_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CartEvent(Base):
    """
    Append-only cart decision. Every event keeps confidence + evidence so a
    shopkeeper can review uncertain vision-based decisions cheaply.
    """

    __tablename__ = "cart_events"
    __table_args__ = (
        Index("ix_cart_events_session_time", "session_id", "timestamp"),
        Index("ix_cart_events_cart_time", "cart_id", "timestamp"),
        Index("ix_cart_events_product_time", "product_id", "timestamp"),
        Index("ix_cart_events_type_time", "event_type", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("virtual_carts.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("store_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    shelf_zone_id = Column(Integer, ForeignKey("shelf_zones.id", ondelete="SET NULL"), nullable=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    global_id = Column(String(100), nullable=True, index=True)
    event_type = Column(String(40), nullable=False, index=True)  # pickup, putback, adjustment, checkout
    quantity_delta = Column(Integer, nullable=False, default=0)
    confidence = Column(Float, nullable=False, default=1.0)
    rule_source = Column(String(60), nullable=False, default="manual")
    evidence = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class LossPreventionAlert(Base):
    """Review queue for unpaid exits, uncertain picks, handoffs, occlusion, etc."""

    __tablename__ = "loss_prevention_alerts"
    __table_args__ = (
        Index("ix_loss_alerts_status_time", "status", "timestamp"),
        Index("ix_loss_alerts_session_time", "session_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("store_sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    cart_id = Column(Integer, ForeignKey("virtual_carts.id", ondelete="SET NULL"), nullable=True)
    alert_type = Column(String(60), nullable=False, index=True)
    severity = Column(String(20), nullable=False, default="medium")
    status = Column(String(30), nullable=False, default="open", index=True)
    description = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    evidence = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
