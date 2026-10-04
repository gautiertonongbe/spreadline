"""Application error taxonomy and safe HTTP rendering.

Error responses carry a stable machine code and a message that is safe to show a
client. Internal detail (SQL, provider URLs, stack context) stays in logs.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger

logger = get_logger(__name__)


class SpreadlineError(Exception):
    """Base class. ``code`` is part of the API contract."""

    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **context: Any) -> None:
        super().__init__(message or self.message)
        self.detail = message or self.message
        self.context = context


class NotFoundError(SpreadlineError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class ValidationError(SpreadlineError):
    status_code = 422
    code = "validation_error"
    message = "The request could not be validated."


class AuthenticationError(SpreadlineError):
    """No valid credential, or the wrong one.

    401 rather than 403: the caller is not identified, as opposed to identified
    and not permitted. The two are different facts and the client acts on them
    differently, so they do not share a status code here.
    """

    status_code = 401
    code = "not_authenticated"
    message = "Sign in to continue."


class ForbiddenError(SpreadlineError):
    """Identified, and not allowed to do this."""

    status_code = 403
    code = "forbidden"
    message = "This account may not do that."


class ConflictError(SpreadlineError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class InsufficientDataError(SpreadlineError):
    """Raised where a caller demands an answer the data cannot support.

    Distinct from a validation error: the request was well-formed, we simply
    refuse to fabricate the result.
    """

    status_code = 422
    code = "insufficient_data"
    message = "Not enough data to produce this result."


class ProviderError(SpreadlineError):
    status_code = 502
    code = "provider_error"
    message = "A data provider request failed."


class ProviderUnavailableError(ProviderError):
    status_code = 503
    code = "provider_unavailable"
    message = "The data provider is unavailable."


class ProviderCapabilityError(ProviderError):
    status_code = 501
    code = "provider_capability_unsupported"
    message = "The provider does not support this capability."


class ProviderNotConfiguredError(ProviderError):
    status_code = 503
    code = "provider_not_configured"
    message = "The provider has no credentials configured."


def _payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    body = {"error": {"code": code, "message": message}}
    if extra:
        body["error"].update(extra)
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SpreadlineError)
    async def _handle_app_error(_: Request, exc: SpreadlineError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error(exc.detail, extra={"context": exc.context}, exc_info=exc)
        else:
            logger.info(exc.detail, extra={"context": exc.context})
        return JSONResponse(status_code=exc.status_code, content=_payload(exc.code, exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(
                "validation_error",
                "The request could not be validated.",
                fields=[
                    {"loc": list(err.get("loc", [])), "msg": err.get("msg")} for err in exc.errors()
                ],
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _handle_db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        # The driver message can contain row values; it is logged, never returned.
        logger.error("database error", exc_info=exc)
        return JSONResponse(
            status_code=500, content=_payload("database_error", "A database error occurred.")
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_payload("internal_error", "An unexpected error occurred."),
        )
