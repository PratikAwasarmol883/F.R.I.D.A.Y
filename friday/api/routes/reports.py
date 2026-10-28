import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.schemas import (
    ReportConflictOut,
    ReportFindingOut,
    ReportOut,
    ReportSourceOut,
    ReportSourceRef,
)
from friday.persistence.database import get_db_session
from friday.services.auth_service import CurrentUser
from friday.services.report_service import ReportService, ReportView

router = APIRouter(prefix="/reports", tags=["reports"])


def to_report_out(view: ReportView) -> ReportOut:
    by_key = {
        s.source_key: ReportSourceRef(
            key=s.source_key, url=s.url, title=s.title, retrieved_at=s.retrieved_at
        )
        for s in view.sources
    }
    content = view.report.content

    def refs(keys: list[str]) -> list[ReportSourceRef]:
        return [by_key[key] for key in keys if key in by_key]

    return ReportOut(
        id=view.report.id,
        question=view.report.question,
        created_at=view.report.created_at,
        summary=view.report.summary,
        findings=[
            ReportFindingOut(
                claim=f["claim"], confidence=f["confidence"], sources=refs(f["source_keys"])
            )
            for f in content["findings"]
        ],
        conflicts=[
            ReportConflictOut(description=c["description"], sources=refs(c["source_keys"]))
            for c in content["conflicts"]
        ],
        limitations=content["limitations"],
        sources=[
            ReportSourceOut(**by_key[s.source_key].model_dump(), content_hash=s.content_hash)
            for s in view.sources
        ],
        markdown=view.markdown,
    )


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(
    report_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ReportOut:
    return to_report_out(await ReportService(session).get(user, report_id))


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await ReportService(session).delete(user, report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
