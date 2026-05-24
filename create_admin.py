import asyncio

from sqlalchemy import select

from backend.core.security import hash_password
from backend.db.models import Admin
from backend.db.session import AsyncSessionLocal


ADMIN_USERNAME = "MainAdmin"
ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "swaraj"


async def create_admin():
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Admin).where(Admin.email == ADMIN_EMAIL))
        if existing.scalar_one_or_none():
            print("Admin user already exists:", ADMIN_EMAIL)
            return

        admin = Admin(
            username=ADMIN_USERNAME,
            email=ADMIN_EMAIL,
            password_hash=hash_password(ADMIN_PASSWORD),
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        print("Admin user created successfully:", admin.username)


if __name__ == "__main__":
    asyncio.run(create_admin())
