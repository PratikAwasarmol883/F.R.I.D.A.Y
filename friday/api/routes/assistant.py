from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.llm import LLMProvider
from friday.api.dependencies import get_current_user, get_llm_provider
from friday.api.schemas import AskRequest, AskResponse, Usage
from friday.core.config import Settings, get_settings
from friday.persistence.database import get_db_session
from friday.services.assistant_service import AssistantService
from friday.services.auth_service import CurrentUser

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    user: CurrentUser = Depends(get_current_user),  # first, so 401 precedes provider config errors
    session: AsyncSession = Depends(get_db_session),
    llm: LLMProvider = Depends(get_llm_provider),
    settings: Settings = Depends(get_settings),
) -> AskResponse:
    result = await AssistantService(session, llm, settings).ask(
        user, body.message, body.conversation_id
    )
    return AskResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        answer=result.answer,
        usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
    )
