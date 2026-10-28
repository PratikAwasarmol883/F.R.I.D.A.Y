import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.research import SourceRecord
from friday.persistence.models import Report, ResearchSource


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        task_id: uuid.UUID | None,
        question: str,
        summary: str,
        content: dict[str, Any],
        sources: Sequence[SourceRecord],
    ) -> Report:
        report = Report(
            id=uuid.uuid4(),
            user_id=user_id,
            task_id=task_id,
            question=question,
            summary=summary,
            content=content,
        )
        self._session.add(report)
        await self._session.flush()
        for source in sources:
            self._session.add(
                ResearchSource(
                    id=uuid.uuid4(),
                    report_id=report.id,
                    source_key=source.key,
                    url=source.url,
                    title=source.title[:300],
                    retrieved_at=source.retrieved_at,
                    content_hash=source.content_hash,
                    excerpt=source.excerpt,
                    content_type=source.content_type,
                    size_bytes=source.size_bytes,
                )
            )
        await self._session.flush()
        return report

    async def get_owned(
        self, report_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Report, list[ResearchSource]] | None:
        report = await self._session.scalar(
            select(Report)
            .where(Report.id == report_id, Report.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if report is None:
            return None
        stmt = (
            select(ResearchSource)
            .where(ResearchSource.report_id == report.id)
            .order_by(ResearchSource.source_key)
        )
        sources = list((await self._session.scalars(stmt)).all())
        sources.sort(key=lambda s: int(s.source_key[1:]))
        return report, sources

    async def delete_owned(self, report_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(Report).where(Report.id == report_id, Report.user_id == user_id)
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_by_task(self, task_id: uuid.UUID) -> Report | None:
        return await self._session.scalar(select(Report).where(Report.task_id == task_id))
