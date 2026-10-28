import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import NotFoundError
from friday.orchestration.report import render_markdown
from friday.persistence.models import Report, ResearchSource
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.reports import ReportRepository
from friday.services.auth_service import CurrentUser


@dataclass(frozen=True)
class ReportView:
    report: Report
    sources: list[ResearchSource]
    markdown: str


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._reports = ReportRepository(session)
        self._audit = AuditRepository(session)

    async def get(self, user: CurrentUser, report_id: uuid.UUID) -> ReportView:
        found = await self._reports.get_owned(report_id, user.user_id)
        if found is None:
            raise NotFoundError
        report, sources = found
        return ReportView(
            report, sources, render_markdown(report.question, report.content, sources)
        )

    async def delete(self, user: CurrentUser, report_id: uuid.UUID) -> None:
        if not await self._reports.delete_owned(report_id, user.user_id):
            raise NotFoundError
        await self._audit.add(
            action="report.delete",
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="report",
            target_id=str(report_id),
        )
        await self._session.commit()
