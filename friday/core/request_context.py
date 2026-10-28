import json
import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from friday.core.errors import error_body
from friday.core.logging_config import get_logger

logger = get_logger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

MAX_BODY_BYTES = 64 * 1024
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class BodyLimitMiddleware:
    """Counts streamed request bytes so chunked uploads without a Content-Length are bounded too.

    On overflow it answers 413 itself and shows the app a disconnect: the app's own error
    handling (which would turn a read failure into a generic 400) never gets to respond.
    """

    def __init__(self, app: Any, path_limits: dict[str, int] | None = None) -> None:
        self.app = app
        self._path_limits = path_limits or {}

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._path_limits.get(scope["path"], MAX_BODY_BYTES)
        received = 0
        rejected = False
        response_started = False

        async def limited_receive() -> Any:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request" and not rejected:
                received += len(message.get("body", b""))
                if received > limit and not response_started:
                    rejected = True
                    body = json.dumps(
                        error_body(
                            "PAYLOAD_TOO_LARGE", "Request body is too large.", request_id_var.get()
                        )
                    ).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
            if rejected:
                return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Any) -> None:
            nonlocal response_started
            if rejected:
                return  # the app's response to a truncated request is meaningless
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, limited_receive, guarded_send)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID, enforces body-size caps, and converts unexpected errors to 500."""

    def __init__(self, app: Any, path_limits: dict[str, int] | None = None) -> None:
        super().__init__(app)
        self._path_limits = path_limits or {}

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            length = request.headers.get("content-length", "")
            limit = self._path_limits.get(request.url.path, MAX_BODY_BYTES)
            if length.isdigit() and int(length) > limit:
                response: Response = JSONResponse(
                    status_code=413,
                    content=error_body(
                        "PAYLOAD_TOO_LARGE", "Request body is too large.", request_id
                    ),
                )
            else:
                try:
                    response = await call_next(request)
                except Exception:
                    logger.exception("Unhandled error")
                    response = JSONResponse(
                        status_code=500,
                        content=error_body(
                            "INTERNAL_ERROR", "An unexpected error occurred.", request_id
                        ),
                    )
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
