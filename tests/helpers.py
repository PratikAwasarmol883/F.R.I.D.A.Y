from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import (
    AuditEvent,
    Message,
    Report,
    ResearchSource,
    ToolExecution,
    UsageRecord,
)

from .conftest import SessionFactory, run_db


def count(factory: SessionFactory, model: type) -> int:
    async def go(session: AsyncSession) -> int:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)

    return run_db(factory, go)


def audit_rows(factory: SessionFactory, action: str) -> list[AuditEvent]:
    async def go(session: AsyncSession) -> list[AuditEvent]:
        stmt = select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.created_at)
        return list((await session.scalars(stmt)).all())

    return run_db(factory, go)


def tool_rows(factory: SessionFactory) -> list[ToolExecution]:
    async def go(session: AsyncSession) -> list[ToolExecution]:
        stmt = select(ToolExecution).order_by(ToolExecution.created_at)
        return list((await session.scalars(stmt)).all())

    return run_db(factory, go)


def message_rows(factory: SessionFactory) -> list[Message]:
    async def go(session: AsyncSession) -> list[Message]:
        stmt = select(Message).order_by(Message.created_at, Message.sequence)
        return list((await session.scalars(stmt)).all())

    return run_db(factory, go)


def usage_rows(factory: SessionFactory) -> list[UsageRecord]:
    async def go(session: AsyncSession) -> list[UsageRecord]:
        stmt = select(UsageRecord).order_by(UsageRecord.created_at)
        return list((await session.scalars(stmt)).all())

    return run_db(factory, go)


def report_rows(factory: SessionFactory) -> list[Report]:
    async def go(session: AsyncSession) -> list[Report]:
        return list((await session.scalars(select(Report).order_by(Report.created_at))).all())

    return run_db(factory, go)


def source_rows(factory: SessionFactory) -> list[ResearchSource]:
    async def go(session: AsyncSession) -> list[ResearchSource]:
        stmt = select(ResearchSource).order_by(ResearchSource.source_key)
        return list((await session.scalars(stmt)).all())

    return run_db(factory, go)
