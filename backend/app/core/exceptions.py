"""Application errors and the handlers that turn every failure into one JSON shape:

    {"code": "...", "message": "...", "details": ..., "request_id": "..."}
"""
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    message = "The request could not be processed."


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have access to this resource."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with existing data."


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The uploaded file is too large."


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"
    message = "Unsupported file type."


class TooManyRequestsError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please slow down."


class NotImplementedYetError(AppError):
    status_code = 501
    code = "not_implemented"
    message = "This feature is not available yet."


class BadGatewayError(AppError):
    """An upstream provider (weather, geocoding, ...) failed or returned garbage."""

    status_code = 502
    code = "upstream_error"
    message = "An upstream service failed."


class GatewayTimeoutError(AppError):
    status_code = 504
    code = "upstream_timeout"
    message = "An upstream service timed out."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "Service temporarily unavailable."


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id_ctx.get(),
        },
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code in (502, 504):  # expected upstream trouble; providers log details
            logger.warning("%s: %s", exc.code, exc.message)
        elif exc.status_code >= 500 and exc.status_code != 501:
            logger.error("%s: %s", exc.code, exc.message, exc_info=exc)
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        try:
            code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
        except ValueError:
            code = f"http_{exc.status_code}"
        return error_response(
            exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in err["loc"] if part != "body"),
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed.", details)
