import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.notify.base import Notifier
from friday.api.dependencies import get_current_user, get_notifier
from friday.api.schemas import APPROVAL_STATUSES, ApprovalDecision, ApprovalOut
from friday.core.config import Settings, get_settings
from friday.persistence.database import get_db_session
from friday.persistence.models import Approval
from friday.services.approval_service import ApprovalService
from friday.services.auth_service import CurrentUser

router = APIRouter(prefix="/approvals", tags=["approvals"])


def to_approval_out(approval: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        tool=approval.tool_name,
        summary=approval.summary,
        arguments=approval.arguments,
        arguments_hash=approval.arguments_hash,
        status=approval.status,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
        finished_at=approval.finished_at,
        error_code=approval.error_code,
    )


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    status: APPROVAL_STATUSES | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[ApprovalOut]:
    rows = await ApprovalService(session, settings).list_for_user(user, status, limit)
    return [to_approval_out(a) for a in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ApprovalOut:
    return to_approval_out(await ApprovalService(session, settings).get(user, approval_id))


@router.post("/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    notifier: Notifier = Depends(get_notifier),
) -> ApprovalOut:
    approval = await ApprovalService(session, settings, notifier).decide(
        user, approval_id, body.decision == "approve", body.arguments_hash
    )
    return to_approval_out(approval)
