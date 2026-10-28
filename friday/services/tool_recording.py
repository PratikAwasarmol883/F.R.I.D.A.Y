import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.enums import AuditOutcome, ToolStatus
from friday.orchestration.executor import ExecutionEntry
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.tool_executions import ToolExecutionRepository
from friday.services.auth_service import CurrentUser

_OUTCOME = {
    ToolStatus.SUCCEEDED: AuditOutcome.SUCCESS,
    ToolStatus.DENIED: AuditOutcome.DENIED,
    ToolStatus.FAILED: AuditOutcome.FAILURE,
    ToolStatus.TIMED_OUT: AuditOutcome.FAILURE,
}


class DbExecutionRecorder:
    """Commits each execution immediately so audit survives a later failure in the same ask."""

    def __init__(
        self,
        session: AsyncSession,
        user: CurrentUser,
        conversation_id: uuid.UUID | None,
        task_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self._user = user
        self._conversation_id = conversation_id
        self._task_id = task_id
        self._executions = ToolExecutionRepository(session)
        self._audit = AuditRepository(session)

    async def record(self, entry: ExecutionEntry) -> uuid.UUID:
        row = await self._executions.add(
            user_id=self._user.user_id,
            device_id=self._user.device_id,
            conversation_id=self._conversation_id,
            task_id=self._task_id,
            tool_name=entry.tool_name,
            tool_version=entry.tool_version,
            risk_class=entry.risk_class,
            status=entry.status,
            arguments_redacted=entry.arguments_redacted,
            arguments_hash=entry.arguments_hash,
            result_meta=entry.result_meta,
            error_code=entry.error_code,
            duration_ms=entry.duration_ms,
        )
        await self._audit.add(
            action="tool.execute",
            outcome=_OUTCOME[entry.status],
            user_id=self._user.user_id,
            device_id=self._user.device_id,
            target_type="tool",
            target_id=entry.tool_name,
        )
        await self._session.commit()
        return row.id
