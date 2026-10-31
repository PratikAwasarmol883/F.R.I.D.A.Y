import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.notify.base import Notifier, NotifyError
from friday.core.config import Settings
from friday.core.credentials import CredentialBox, CredentialError
from friday.domain.approval import ApprovalStatus, action_hash
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import (
    ApprovalChangedError,
    ApprovalExpiredError,
    ApprovalLimitError,
    ApprovalNotPendingError,
    ForbiddenError,
    NotFoundError,
)
from friday.domain.scopes import DECIDE_APPROVALS
from friday.orchestration.tool_contracts import Tool
from friday.persistence.models import Approval
from friday.persistence.repositories.approvals import ApprovalRepository
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.devices import DeviceRepository
from friday.persistence.repositories.integrations import IntegrationRepository
from friday.services.auth_service import CurrentUser
from friday.services.integration_service import PROVIDER_TOOLS
from friday.tools.notify import PROVIDER, TOOL_NAME, NotifyInput, SendNotificationTool

_TOOL_PROVIDERS = {tool: provider for provider, tool in PROVIDER_TOOLS.items()}


class ApprovalService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        notifier: Notifier | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._notifier = notifier
        self._approvals = ApprovalRepository(session)
        self._audit = AuditRepository(session)

    async def _record(
        self,
        action: str,
        approval: Approval,
        user: CurrentUser,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
    ) -> None:
        await self._audit.add(
            action=action,
            outcome=outcome,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="approval",
            target_id=str(approval.id),
        )

    async def _maintain(self, user: CurrentUser) -> None:
        await self._approvals.expire_overdue(user.user_id)
        await self._approvals.fail_stale_executions(
            user.user_id, self._settings.approval_exec_stale_seconds
        )

    async def propose(
        self,
        user: CurrentUser,
        conversation_id: uuid.UUID | None,
        tool: Tool[Any, Any],
        arguments: BaseModel,
    ) -> uuid.UUID:
        await self._maintain(user)
        if (
            await self._approvals.count_pending(user.user_id)
            >= self._settings.max_pending_approvals
        ):
            await self._session.commit()
            raise ApprovalLimitError
        contract = tool.contract
        stored = arguments.model_dump(mode="json")
        approval = await self._approvals.create(
            user_id=user.user_id,
            device_id=user.device_id,
            conversation_id=conversation_id,
            tool_name=contract.name,
            tool_version=contract.version,
            risk_class=str(contract.risk_class),
            arguments=stored,
            arguments_hash=action_hash(contract.name, contract.version, stored),
            summary=tool.describe_action(arguments),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._settings.approval_ttl_seconds),
        )
        await self._record("approval.request", approval, user)
        await self._session.commit()
        return approval.id

    async def list_for_user(
        self, user: CurrentUser, status: str | None, limit: int
    ) -> list[Approval]:
        await self._maintain(user)
        rows = await self._approvals.list_for_user(user.user_id, status, limit)
        await self._session.commit()
        return rows

    async def get(self, user: CurrentUser, approval_id: uuid.UUID) -> Approval:
        await self._maintain(user)
        approval = await self._approvals.get_owned(approval_id, user.user_id)
        await self._session.commit()
        if approval is None:
            raise NotFoundError
        return approval

    async def decide(
        self, user: CurrentUser, approval_id: uuid.UUID, approve: bool, arguments_hash: str
    ) -> Approval:
        if DECIDE_APPROVALS not in user.scopes:
            raise ForbiddenError
        approval = await self.get(user, approval_id)
        if approval.status == ApprovalStatus.EXPIRED.value:
            raise ApprovalExpiredError
        if approval.status != ApprovalStatus.PENDING.value:
            raise ApprovalNotPendingError
        if arguments_hash != approval.arguments_hash:
            raise ApprovalChangedError  # the user saw a different action than the stored one

        if not approve:
            if not await self._approvals.reject(approval_id, user.user_id, user.device_id):
                raise ApprovalNotPendingError
            await self._record("approval.reject", approval, user)
            await self._session.commit()
            return await self.get(user, approval_id)

        claimed = await self._approvals.claim(approval_id, user.user_id, user.device_id)
        if claimed is None:  # lost a race, or it expired in the meantime
            await self._session.rollback()
            current = await self.get(user, approval_id)
            raise (
                ApprovalExpiredError
                if current.status == ApprovalStatus.EXPIRED.value
                or current.expires_at <= datetime.now(UTC)
                else ApprovalNotPendingError
            )
        await self._record("approval.approve", claimed, user)
        await self._session.commit()  # the claim is durable before anything is sent

        error = await self._execute(user, claimed)
        await self._approvals.finish(
            approval_id, ApprovalStatus.FAILED if error else ApprovalStatus.EXECUTED, error
        )
        await self._record(
            "approval.execute",
            claimed,
            user,
            AuditOutcome.FAILURE if error else AuditOutcome.SUCCESS,
        )
        await self._session.commit()
        return await self.get(user, approval_id)

    async def _execute(self, user: CurrentUser, approval: Approval) -> str | None:
        """Re-validate everything at the moment of use. Returns a stable error code or None."""
        if approval.arguments_hash != action_hash(
            approval.tool_name, approval.tool_version, approval.arguments
        ):
            return "tampered"
        if approval.tool_name not in self._settings.enabled_tool_names:
            return "tool_disabled"
        if approval.tool_name != TOOL_NAME or _TOOL_PROVIDERS.get(approval.tool_name) != PROVIDER:
            return "unsupported_tool"
        device = (
            await DeviceRepository(self._session).get(approval.device_id)
            if approval.device_id
            else None
        )
        if device is None or device.revoked_at is not None or device.user_id != user.user_id:
            return "device_revoked"
        connection = await IntegrationRepository(self._session).get_active(user.user_id, PROVIDER)
        if connection is None or not connection.credential:
            return "integration_revoked"
        if self._notifier is None or self._settings.integration_key is None:
            return "integration_unavailable"
        try:
            topic = CredentialBox(self._settings.integration_key).open(connection.credential)
        except CredentialError:
            return "credential_error"
        try:
            arguments = NotifyInput.model_validate(approval.arguments, strict=True)
        except ValidationError:
            return "invalid_arguments"

        notifier = self._notifier

        async def send(title: str, message: str) -> None:
            await notifier.send(topic, title, message, self._settings.notify_timeout_seconds)

        tool = SendNotificationTool(send, self._settings.notification_daily_limit)
        try:
            async with asyncio.timeout(tool.contract.timeout_seconds):
                await tool.run(arguments)
        except TimeoutError:
            return "timeout"
        except NotifyError as exc:
            return exc.code
        return None
