import asyncio
import json

import httpx
import pytest

from friday.adapters.notify.base import NotifyError
from friday.adapters.notify.ntfy import NtfyNotifier


def notifier(transport: httpx.MockTransport) -> NtfyNotifier:
    client = httpx.AsyncClient(transport=transport, follow_redirects=False)
    return NtfyNotifier("https://ntfy.example.com/", client)


def send(n: NtfyNotifier) -> None:
    asyncio.run(n.send("my-topic-1234", "Title", "Body text", 5.0))


def test_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "x"})

    send(notifier(httpx.MockTransport(handler)))
    [request] = seen
    assert request.method == "POST" and str(request.url) == "https://ntfy.example.com"
    assert json.loads(request.content) == {
        "topic": "my-topic-1234",
        "title": "Title",
        "message": "Body text",
    }
    assert "authorization" not in request.headers


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "rejected"),
        (401, "rejected"),
        (403, "rejected"),
        (404, "rejected"),
        (302, "rejected"),
        (429, "unavailable"),
        (500, "unavailable"),
        (503, "unavailable"),
    ],
)
def test_statuses_map_to_stable_codes_and_redirects_are_not_followed(
    status: int, code: str
) -> None:
    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        return httpx.Response(
            status,
            headers={"location": "http://169.254.169.254/"},
            text="provider detail my-topic-1234",
        )

    with pytest.raises(NotifyError) as caught:
        send(notifier(httpx.MockTransport(handler)))
    assert caught.value.code == code and len(hops) == 1
    assert "my-topic-1234" not in str(caught.value)
    assert "provider detail" not in repr(caught.value)


def test_timeouts_and_connection_errors() -> None:
    def slow(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(NotifyError, match="timeout"):
        send(notifier(httpx.MockTransport(slow)))
    with pytest.raises(NotifyError, match="unavailable"):
        send(notifier(httpx.MockTransport(down)))
