"""
Create (or promote) an admin user for the OmniTrack DB admin panel.

Usage:
    python scripts/create_admin.py <username> <email> <password>
    python scripts/create_admin.py admin admin@local.test ChangeMe123!

If the user already exists, the password is reset and the role is set to ADMIN.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.security.jwt_handler import hash_password


async def upsert_admin(username: str, email: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                email=email,
                hashed_password=hash_password(password),
                full_name="Administrator",
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(user)
            action = "created"
        else:
            user.email = email
            user.hashed_password = hash_password(password)
            user.role = UserRole.ADMIN
            user.is_active = True
            action = "updated"
        await db.commit()
        print(f"Admin user {action}: {username} ({email})")


def main() -> None:
    if len(sys.argv) != 4:
        print("Usage: python scripts/create_admin.py <username> <email> <password>")
        sys.exit(2)
    _, username, email, password = sys.argv
    asyncio.run(upsert_admin(username, email, password))


if __name__ == "__main__":
    main()
