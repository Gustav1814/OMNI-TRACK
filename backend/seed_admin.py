import asyncio
from app.database import engine, Base, AsyncSessionLocal
from app.models.user import User, UserRole
from app.security.jwt_handler import hash_password
from sqlalchemy import select

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.username == "admin"))
        if not res.scalar_one_or_none():
            admin_user = User(
                username="admin", 
                email="admin@example.com", 
                hashed_password=hash_password("admin"), 
                role=UserRole.ADMIN, 
                full_name="Admin", 
                is_active=True
            )
            session.add(admin_user)
            await session.commit()
            print("Admin user created.")
        else:
            print("Admin user already exists.")

if __name__ == "__main__":
    asyncio.run(seed())
