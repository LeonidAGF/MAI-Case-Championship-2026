"""Single error envelope for the whole API: ``{"code": ..., "message": ..., "details": ...}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ApiError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details
        self.headers = headers

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


class Unauthorized(ApiError):
    status_code = 401
    code = "unauthorized"


class Forbidden(ApiError):
    status_code = 403
    code = "forbidden"


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class ValidationFailed(ApiError):
    status_code = 422
    code = "validation_error"


class ConflictError(ApiError):
    status_code = 409
    code = "conflict"


class ServiceUnavailable(ApiError):
    status_code = 503
    code = "service_unavailable"


class RateLimited(ApiError):
    status_code = 429
    code = "rate_limited"


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    414: "uri_too_long",
    415: "unsupported_media_type",
}


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    out = []
    for err in exc.errors():
        out.append(
            {
                "loc": [str(p) for p in err.get("loc", ())],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.to_body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = {
            "code": "validation_error",
            "message": "Некорректные параметры запроса",
            "details": _validation_details(exc),
        }
        return JSONResponse(body, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else code
        if exc.status_code == 404 and message == "Not Found":
            message = "Ресурс не найден"
        return JSONResponse({"code": code, "message": message}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover - safety net
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            {"code": "internal_error", "message": "Внутренняя ошибка сервера"}, status_code=500
        )


# Reusable OpenAPI response docs
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Не найдено"},
    422: {"model": ErrorResponse, "description": "Ошибка валидации"},
    429: {"model": ErrorResponse, "description": "Превышен лимит запросов"},
}
# Routes that need an organisation's API key (/v1).
KEY_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Нет ключа API или ключ неверный"},
    403: {"model": ErrorResponse, "description": "Ключ отозван, истёк или не даёт этого доступа"},
    **ERROR_RESPONSES,
}
