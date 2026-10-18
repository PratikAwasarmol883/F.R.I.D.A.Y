from typing import Any


def error_body(
    code: str, message: str, request_id: str | None, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Stable error envelope returned for every non-2xx response."""
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = details
    return body
