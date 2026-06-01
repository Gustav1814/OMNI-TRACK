"""SQLAlchemy models — imported by Alembic for autogenerate."""

from app.models.user import User, UserRole
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.embedding import Embedding, EMBEDDING_DIM
from app.models.audit_log import AuditLog
from app.models.analytics import (
    FootTraffic,
    CustomerJourney,
    DemographicSnapshot,
    StoreVibeScore,
    PeakHoursData,
)
from app.models.humanless import (
    Product,
    ShelfZone,
    StoreSession,
    VirtualCart,
    CartItem,
    CartEvent,
    LossPreventionAlert,
)

__all__ = [
    "User",
    "UserRole",
    "Camera",
    "Detection",
    "Embedding",
    "EMBEDDING_DIM",
    "AuditLog",
    "FootTraffic",
    "CustomerJourney",
    "DemographicSnapshot",
    "StoreVibeScore",
    "PeakHoursData",
    "Product",
    "ShelfZone",
    "StoreSession",
    "VirtualCart",
    "CartItem",
    "CartEvent",
    "LossPreventionAlert",
]
