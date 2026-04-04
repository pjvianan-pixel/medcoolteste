#!/usr/bin/env python
"""CLI script to create an admin user.

Usage:
    cd backend
    python scripts/create_admin.py --email admin@example.com --password secret123

The DATABASE_URL environment variable (or backend/.env file) must be set and
point to a running PostgreSQL instance with migrations already applied.
"""
import argparse
import asyncio
import sys
import uuid
from pathlib import Path

# Allow running from the backend/ directory or the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env if present so the script can be run without pre-exporting env vars.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass  # python-dotenv is optional; user can export vars manually

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import hash_password
from app.db.models.user import User, UserRole


async def create_admin(email: str, password: str) -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await _ensure_admin(session, email, password)

    await engine.dispose()


async def _ensure_admin(session: AsyncSession, email: str, password: str) -> None:
    result = await session.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing is not None:
        print(f"[error] A user with email '{email}' already exists (role: {existing.role}).")
        sys.exit(1)

    admin = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.admin,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    print(f"[ok] Admin user created: {email} (id={admin.id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an admin user.")
    parser.add_argument("--email", required=True, help="Admin e-mail address")
    parser.add_argument("--password", required=True, help="Admin password (plain-text)")
    args = parser.parse_args()

    asyncio.run(create_admin(args.email, args.password))


if __name__ == "__main__":
    main()
