"""Push notifications through an ntfy-compatible server using its JSON publish API."""

import httpx

from friday.adapters.notify.base import NotifyError
from friday.core.logging_config import get_logger

logger = get_logger(__name__)


class NtfyNotifier:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        # The host is operator configuration, never user input; redirects are not followed.
        self._client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, topic: str, title: str, message: str, timeout_seconds: float) -> None:
        try:
            response = await self._client.post(
                self._base_url,
                json={"topic": topic, "title": title, "message": message},
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise NotifyError("timeout") from exc
        except httpx.HTTPError as exc:
            raise NotifyError("unavailable") from exc
        status = response.status_code
        if 200 <= status < 300:
            return
        logger.warning("Notification provider returned status %s", status)
        raise NotifyError("unavailable" if status == 429 or status >= 500 else "rejected")
