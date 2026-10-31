from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.config import Settings
from friday.core.credentials import CredentialBox
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import ForbiddenError, IntegrationUnavailableError, NotFoundError
from friday.domain.scopes import MANAGE_INTEGRATIONS
from friday.persistence.models import IntegrationConnection
from friday.persistence.repositories.approvals import ApprovalRepository
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.integrations import IntegrationRepository
from friday.services.auth_service import CurrentUser
from friday.tools.notify import PROVIDER, TOOL_NAME

PROVIDER_TOOLS = {PROVIDER: TOOL_NAME}


class IntegrationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._integrations = IntegrationRepository(session)
        self._approvals = ApprovalRepository(session)
        self._audit = AuditRepository(session)

    def _require_manage(self, user: CurrentUser) -> None:
        if MANAGE_INTEGRATIONS not in user.scopes:
            raise ForbiddenError

    async def connect(self, user: CurrentUser, provider: str, secret: str) -> IntegrationConnection:
        self._require_manage(user)
        if provider not in PROVIDER_TOOLS:
            raise NotFoundError
        if not self._settings.integrations_configured or self._settings.integration_key is None:
            raise IntegrationUnavailableError
        connection = await self._integrations.connect(
            user.user_id, provider, CredentialBox(self._settings.integration_key).seal(secret)
        )
        await self._record("integration.connect", user, connection)
        await self._session.commit()
        return connection

    async def list_for_user(self, user: CurrentUser) -> list[IntegrationConnection]:
        return await self._integrations.list_for_user(user.user_id)

    async def revoke(self, user: CurrentUser, provider: str) -> None:
        self._require_manage(user)
        if provider not in PROVIDER_TOOLS:
            raise NotFoundError
        if not await self._integrations.revoke(user.user_id, provider):
            raise NotFoundError
        # Proposals that were waiting on this integration can no longer be approved into action.
        await self._approvals.reject_pending_for_tool(user.user_id, PROVIDER_TOOLS[provider])
        await self._audit.add(
            action="integration.revoke",
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="integration",
            target_id=provider,
        )
        await self._session.commit()

    async def _record(
        self, action: str, user: CurrentUser, connection: IntegrationConnection
    ) -> None:
        await self._audit.add(
            action=action,
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="integration",
            target_id=str(connection.id),
        )
