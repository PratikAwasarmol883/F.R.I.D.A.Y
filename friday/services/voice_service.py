import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.speech.base import SpeechProvider, SynthesizedAudio, TranscriptionProvider
from friday.core.config import Settings
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import (
    DomainError,
    NoSpeechError,
    NotFoundError,
    PayloadTooLargeError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedMediaError,
    VoiceLimitError,
)
from friday.domain.task_inputs import MAX_MESSAGE_CHARS
from friday.orchestration.audio import detect_audio_format
from friday.orchestration.speech_text import speakable
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.conversations import ConversationRepository
from friday.services.assistant_service import AskResult, AssistantService
from friday.services.auth_service import CurrentUser

ATTEMPT_ACTION = "assistant.voice.attempt"
RESULT_ACTION = "assistant.voice"


@dataclass(frozen=True)
class VoiceResult:
    transcript: str
    ask: AskResult
    audio: SynthesizedAudio | None
    speech_error: str | None


def _speech_error_code(exc: ProviderError) -> str:
    if isinstance(exc, ProviderTimeoutError):
        return "provider_timeout"
    if isinstance(exc, ProviderUnavailableError):
        return "provider_unavailable"
    return "provider_error"


class VoiceService:
    """Audio in, spoken answer out. Audio is never stored and never logged."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        stt: TranscriptionProvider,
        tts: SpeechProvider,
        assistant: AssistantService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._stt = stt
        self._tts = tts
        self._assistant = assistant
        self._audit = AuditRepository(session)
        self._conversations = ConversationRepository(session)

    async def handle(
        self,
        user: CurrentUser,
        audio: bytes,
        conversation_id: uuid.UUID | None,
        speak: bool,
    ) -> VoiceResult:
        audio_format = detect_audio_format(audio)
        if audio_format is None:
            await self._record(user, AuditOutcome.DENIED)
            raise UnsupportedMediaError
        if conversation_id is not None:  # fail before any paid call
            if await self._conversations.get_owned(conversation_id, user.user_id) is None:
                raise NotFoundError
        await self._enforce_daily_limit(user)

        # The attempt is recorded and committed before any paid call (no DB transaction is held
        # across the network), so even failed requests count toward the daily limit.
        await self._audit.add(
            action=ATTEMPT_ACTION,
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
        )
        await self._session.commit()
        try:
            transcript = await self._transcribe(audio, audio_format)
            result = await self._assistant.ask(user, transcript, conversation_id, style="voice")
        except DomainError:
            await self._session.rollback()
            await self._record(user, AuditOutcome.FAILURE)
            raise

        speech, speech_error = await self._speak(result.answer) if speak else (None, None)
        await self._record(user, AuditOutcome.SUCCESS)
        return VoiceResult(transcript, result, speech, speech_error)

    async def _enforce_daily_limit(self, user: CurrentUser) -> None:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        used = await self._audit.count_since(user.user_id, ATTEMPT_ACTION, start)
        if used >= self._settings.voice_daily_limit:
            await self._record(user, AuditOutcome.DENIED)
            raise VoiceLimitError

    async def _transcribe(self, audio: bytes, audio_format: str) -> str:
        heard = await self._stt.transcribe(
            audio, audio_format, self._settings.voice_timeout_seconds
        )
        text = heard.text.strip()
        if not text:
            raise NoSpeechError
        if len(text) > MAX_MESSAGE_CHARS:
            raise PayloadTooLargeError
        return text

    async def _speak(self, answer: str) -> tuple[SynthesizedAudio | None, str | None]:
        spoken = speakable(answer, self._settings.voice_tts_max_chars)
        if not spoken:
            return None, None
        try:
            audio = await self._tts.synthesize(spoken, self._settings.voice_timeout_seconds)
        except ProviderError as exc:
            return None, _speech_error_code(exc)  # degrade to text; the client speaks it locally
        return audio, None

    async def _record(self, user: CurrentUser, outcome: AuditOutcome) -> None:
        await self._audit.add(
            action=RESULT_ACTION, outcome=outcome, user_id=user.user_id, device_id=user.device_id
        )
        await self._session.commit()
