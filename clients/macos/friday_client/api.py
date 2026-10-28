"""HTTP client for the FRIDAY voice endpoint. Never logs or exposes the token."""

import base64
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 25_000_000
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ApiError(Exception):
    """A failure talking to the server. `code` is stable; the message never holds server text."""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class VoiceReply:
    transcript: str
    answer: str
    conversation_id: str
    audio: bytes | None
    audio_format: str | None
    speech_error: str | None


def validate_base_url(url: str) -> str:
    """https only, except for a server on this machine."""
    parts = urlsplit(url)
    local = parts.hostname in _LOCAL_HOSTS
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("The server URL must use https (http is allowed only for localhost)")
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The server URL must be a plain URL without credentials or query")
    return url.rstrip("/")


class FridayApi:
    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], str | None],
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = validate_base_url(base_url)
        self._token_provider = token_provider
        self._timeout = timeout_seconds
        self._client = httpx.Client(transport=transport, follow_redirects=False, trust_env=False)

    def __repr__(self) -> str:
        return f"FridayApi(base_url={self._base_url!r})"

    def close(self) -> None:
        self._client.close()

    def ask_voice(
        self, wav: bytes, conversation_id: str | None = None, *, speak: bool = True
    ) -> VoiceReply:
        token = self._token_provider()
        if not token:
            raise ApiError("UNAUTHENTICATED")
        form = {"speak": "true" if speak else "false"}
        if conversation_id:
            form["conversation_id"] = conversation_id
        try:
            response = self._client.post(
                f"{self._base_url}/api/v1/assistant/voice",
                headers={"Authorization": f"Bearer {token}"},
                files={"audio": ("recording.wav", wav, "audio/wav")},
                data=form,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ApiError("TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise ApiError("NETWORK_ERROR") from exc
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> VoiceReply:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ApiError("BAD_RESPONSE", response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError("BAD_RESPONSE", response.status_code) from exc
        if response.status_code != 200:
            code = body.get("code") if isinstance(body, dict) else None
            raise ApiError(code if isinstance(code, str) else "BAD_RESPONSE", response.status_code)
        try:
            audio = body.get("audio")
            return VoiceReply(
                transcript=str(body["transcript"]),
                answer=str(body["answer"]),
                conversation_id=str(body["conversation_id"]),
                audio=base64.b64decode(audio["base64"], validate=True) if audio else None,
                audio_format=str(audio["format"]) if audio else None,
                speech_error=body.get("speech_error"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError("BAD_RESPONSE", response.status_code) from exc
