from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from friday.core.errors import error_body
from friday.core.request_context import request_id_var
from friday.domain.exceptions import (
    BudgetExceededError,
    DomainError,
    NotAuthenticatedError,
    NotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

# Most specific first: ProviderError is the base of the other two provider errors.
_DOMAIN_ERRORS: list[tuple[type[DomainError], int, str, str]] = [
    (NotAuthenticatedError, 401, "UNAUTHENTICATED", "Authentication is required or invalid."),
    (NotFoundError, 404, "NOT_FOUND", "Resource not found."),
    (BudgetExceededError, 429, "BUDGET_EXCEEDED", "The daily usage budget has been reached."),
    (ProviderTimeoutError, 504, "PROVIDER_TIMEOUT", "The assistant took too long to respond."),
    (
        ProviderUnavailableError,
        503,
        "PROVIDER_UNAVAILABLE",
        "The assistant is temporarily unavailable.",
    ),
    (ProviderError, 502, "PROVIDER_ERROR", "The assistant could not complete the request."),
]


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    for error_type, status, code, message in _DOMAIN_ERRORS:
        if isinstance(exc, error_type):
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            return JSONResponse(
                status_code=status,
                content=error_body(code, message, request_id_var.get()),
                headers=headers,
            )
    return JSONResponse(
        status_code=500,
        content=error_body("INTERNAL_ERROR", "An unexpected error occurred.", request_id_var.get()),
    )


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Report field paths and error types only; never echo submitted values.
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "type": err["type"]}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_body(
            "VALIDATION_ERROR", "The request is invalid.", request_id_var.get(), details
        ),
    )


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    message = (
        "Resource not found." if exc.status_code == 404 else "The request could not be served."
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, message, request_id_var.get()),
        headers=getattr(exc, "headers", None),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
