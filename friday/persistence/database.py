"""Database engine and session management."""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from friday.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _driver_url(url: str) -> str:
    """Pin the psycopg3 driver regardless of how the URL was written."""
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        _driver_url(settings.database_url),
        echo=settings.sqlalchemy_echo,
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_sessionmaker()() as session:
        yield session
