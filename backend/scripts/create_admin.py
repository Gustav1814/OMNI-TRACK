"""
Create (or promote) an admin user for the OmniTrack DB admin panel.

Usage:
    python scripts/create_admin.py <username> <email>          # prompts for the password (recommended)
    OMNITRACK_ADMIN_PASSWORD=... python scripts/create_admin.py <username> <email>
    python scripts/create_admin.py <username> <email> <password>   # legacy; leaks into shell history

If the user already exists, the password is reset and the role is set to ADMIN.
The password must pass the same policy as the API (length, not containing the username, not repetitive).
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: `python scripts/create_admin.py`

from fastapi import HTTPException
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.routers.auth import _validate_password
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


def _read_password(argv: list[str]) -> str:
    if len(argv) == 4:
        print("Warning: a password on the command line is saved in shell history. "
              "Omit it to be prompted instead.", file=sys.stderr)
        return argv[3]
    env = os.environ.get("OMNITRACK_ADMIN_PASSWORD")
    if env:
        return env
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(2)
    return first


def main() -> None:
    if len(sys.argv) not in (3, 4):
        print("Usage: python scripts/create_admin.py <username> <email> [password]")
        sys.exit(2)
    username, email = sys.argv[1], sys.argv[2]
    password = _read_password(sys.argv)
    try:
        _validate_password(password, username)
    except HTTPException as exc:
        print(f"Password rejected: {exc.detail}", file=sys.stderr)
        sys.exit(2)
    asyncio.run(upsert_admin(username, email, password))


if __name__ == "__main__":
    main()
