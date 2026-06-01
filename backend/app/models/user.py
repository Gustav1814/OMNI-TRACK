"""
OmniTrack AI — User Model
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SQLEnum, Index
from sqlalchemy.sql import func
from app.database import Base
import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=True)
    role = Column(
        SQLEnum(
            UserRole,
            name="userrole",
            values_callable=lambda roles: [role.value for role in roles],
        ),
        default=UserRole.VIEWER,
        nullable=False,
    )
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
