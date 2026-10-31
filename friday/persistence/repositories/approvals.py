import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.approval import ApprovalStatus
from friday.persistence.models import Approval

_DECIDED = (
    ApprovalStatus.EXECUTED,
    ApprovalStatus.FAILED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
)


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID | None,
        conversation_id: uuid.UUID | None,
        tool_name: str,
        tool_version: str,
        risk_class: str,
        arguments: dict[str, Any],
        arguments_hash: str,
        summary: str,
        expires_at: datetime,
    ) -> Approval:
        approval = Approval(
            id=uuid.uuid4(),
            user_id=user_id,
            device_id=device_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            tool_version=tool_version,
            risk_class=risk_class,
            arguments=arguments,
            arguments_hash=arguments_hash,
            summary=summary,
            status=ApprovalStatus.PENDING.value,
            expires_at=expires_at,
        )
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get_owned(self, approval_id: uuid.UUID, user_id: uuid.UUID) -> Approval | None:
        stmt = (
            select(Approval)
            .where(Approval.id == approval_id, Approval.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def list_for_user(
        self, user_id: uuid.UUID, status: str | None, limit: int
    ) -> list[Approval]:
        stmt = select(Approval).where(Approval.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Approval.status == status)
        stmt = stmt.order_by(Approval.created_at.desc(), Approval.id).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    async def count_pending(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.expires_at > func.now(),
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def count_created_since(self, user_id: uuid.UUID, tool_name: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.tool_name == tool_name,
                Approval.created_at >= since,
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def claim(
        self, approval_id: uuid.UUID, user_id: uuid.UUID, decided_by: uuid.UUID
    ) -> Approval | None:
        """Atomically move pending -> executing. Only one caller can ever win, and never late."""
        stmt = (
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.user_id == user_id,
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.expires_at > func.now(),
            )
            .values(
                status=ApprovalStatus.EXECUTING.value,
                decided_at=func.now(),
                decided_by_device_id=decided_by,
            )
            .returning(Approval)
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def reject(
        self,
        approval_id: uuid.UUID,
        user_id: uuid.UUID,
        decided_by: uuid.UUID | None,
        error_code: str | None = None,
    ) -> bool:
        stmt = (
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.user_id == user_id,
                Approval.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=ApprovalStatus.REJECTED.value,
                decided_at=func.now(),
                finished_at=func.now(),
                decided_by_device_id=decided_by,
                error_code=error_code,
            )
        )
        return bool(getattr(await self._session.execute(stmt), "rowcount", 0))

    async def finish(
        self, approval_id: uuid.UUID, status: ApprovalStatus, error_code: str | None
    ) -> bool:
        stmt = (
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.EXECUTING.value)
            .values(status=status.value, finished_at=func.now(), error_code=error_code)
        )
        return bool(getattr(await self._session.execute(stmt), "rowcount", 0))

    async def expire_overdue(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.expires_at <= func.now(),
            )
            .values(status=ApprovalStatus.EXPIRED.value, finished_at=func.now())
        )
        return int(getattr(await self._session.execute(stmt), "rowcount", 0) or 0)

    async def fail_stale_executions(self, user_id: uuid.UUID, older_than_seconds: int) -> int:
        """A claim that never finished is failed, never retried: at most once."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        stmt = (
            update(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.status == ApprovalStatus.EXECUTING.value,
                Approval.decided_at <= cutoff,
            )
            .values(
                status=ApprovalStatus.FAILED.value,
                finished_at=func.now(),
                error_code="interrupted",
            )
        )
        return int(getattr(await self._session.execute(stmt), "rowcount", 0) or 0)

    async def reject_pending_for_device(self, device_id: uuid.UUID) -> int:
        stmt = (
            update(Approval)
            .where(Approval.device_id == device_id, Approval.status == ApprovalStatus.PENDING.value)
            .values(
                status=ApprovalStatus.REJECTED.value,
                finished_at=func.now(),
                error_code="device_revoked",
            )
        )
        return int(getattr(await self._session.execute(stmt), "rowcount", 0) or 0)

    async def reject_pending_for_tool(self, user_id: uuid.UUID, tool_name: str) -> int:
        stmt = (
            update(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.tool_name == tool_name,
                Approval.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=ApprovalStatus.REJECTED.value,
                finished_at=func.now(),
                error_code="integration_revoked",
            )
        )
        return int(getattr(await self._session.execute(stmt), "rowcount", 0) or 0)

    async def purge_decided(self, older_than: datetime) -> int:
        stmt = delete(Approval).where(
            Approval.status.in_([s.value for s in _DECIDED]), Approval.finished_at < older_than
        )
        return int(getattr(await self._session.execute(stmt), "rowcount", 0) or 0)
