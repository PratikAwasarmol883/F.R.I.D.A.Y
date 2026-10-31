import uuid
from typing import Any

from pydantic import BaseModel

from friday.domain.exceptions import ApprovalLimitError
from friday.orchestration.tool_contracts import Tool, ToolError
from friday.services.approval_service import ApprovalService
from friday.services.auth_service import CurrentUser


class ApprovalProposals:
    """Lets the tool loop store a gated call as an approval for the current user."""

    def __init__(
        self, service: ApprovalService, user: CurrentUser, conversation_id: uuid.UUID | None
    ) -> None:
        self._service = service
        self._user = user
        self._conversation_id = conversation_id

    async def propose(self, tool: Tool[Any, Any], arguments: BaseModel) -> uuid.UUID:
        try:
            return await self._service.propose(self._user, self._conversation_id, tool, arguments)
        except ApprovalLimitError as exc:
            raise ToolError("approval_limit") from exc
