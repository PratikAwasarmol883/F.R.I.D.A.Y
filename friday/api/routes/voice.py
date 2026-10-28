import base64
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.llm import LLMProvider
from friday.api.dependencies import (
    get_current_user,
    get_llm_provider,
    get_speech_provider,
    get_tool_registry,
)
from friday.api.schemas import AudioOut, SourceOut, ToolUseOut, Usage, VoiceResponse
from friday.composition import LazySpeech
from friday.core.config import Settings, get_settings
from friday.domain.exceptions import PayloadTooLargeError
from friday.orchestration.tool_registry import ToolRegistry
from friday.persistence.database import get_db_session
from friday.services.assistant_service import AssistantService
from friday.services.auth_service import CurrentUser
from friday.services.voice_service import VoiceService

router = APIRouter(prefix="/assistant", tags=["voice"])


@router.post("/voice", response_model=VoiceResponse)
async def voice(
    audio: UploadFile = File(...),
    conversation_id: uuid.UUID | None = Form(default=None),
    speak: bool = Form(default=True),
    user: CurrentUser = Depends(get_current_user),  # first, so 401 precedes configuration errors
    session: AsyncSession = Depends(get_db_session),
    llm: LLMProvider = Depends(get_llm_provider),
    speech: LazySpeech = Depends(get_speech_provider),
    settings: Settings = Depends(get_settings),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> VoiceResponse:
    data = await audio.read(settings.voice_max_upload_bytes + 1)
    if len(data) > settings.voice_max_upload_bytes:
        raise PayloadTooLargeError
    assistant = AssistantService(session, llm, settings, registry)
    result = await VoiceService(session, settings, speech, speech, assistant).handle(
        user, data, conversation_id, speak
    )
    ask = result.ask
    return VoiceResponse(
        transcript=result.transcript,
        answer=ask.answer,
        conversation_id=ask.conversation_id,
        message_id=ask.message_id,
        usage=Usage(input_tokens=ask.input_tokens, output_tokens=ask.output_tokens),
        sources=[SourceOut(title=s.title, url=s.url) for s in ask.sources],
        tools_used=[ToolUseOut(name=u.name, status=str(u.status)) for u in ask.tools_used],
        audio=(
            AudioOut(
                format=result.audio.audio_format,
                base64=base64.b64encode(result.audio.data).decode("ascii"),
            )
            if result.audio
            else None
        ),
        speech_error=result.speech_error,
    )
