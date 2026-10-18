import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from friday.core.errors import error_body
from friday.core.logging_config import get_logger

logger = get_logger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

MAX_BODY_BYTES = 64 * 1024
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID, enforces the body-size cap, and converts unexpected errors to 500."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > MAX_BODY_BYTES:
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
