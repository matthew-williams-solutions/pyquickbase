"""Turn HTTP responses and failures into results or exceptions. No network access."""

from typing import Any

import httpx

from ._retry import parse_retry_after
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
)

REQUEST_ID_HEADER = "qb-api-ray"
_MAX_DETAIL_CHARS = 500
_ERRORS_BY_STATUS: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
}


def _error_details(response: httpx.Response) -> tuple[str | None, str | None]:
    """Return Quickbase's message and description, falling back to the raw body."""
    try:
        body: Any = response.json()
    except ValueError:
        return (response.text[:_MAX_DETAIL_CHARS] or None), None
    if not isinstance(body, dict):
        return None, None
    details: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
    # A 429 from the rate limiter has only `error`.
    message, description = (
        details.get("message") or details.get("error"),
        details.get("description"),
    )
    return (
        message[:_MAX_DETAIL_CHARS] if isinstance(message, str) and message else None,
        description[:_MAX_DETAIL_CHARS] if isinstance(description, str) and description else None,
    )


def error_from_response(response: httpx.Response, method: str, path: str) -> APIError:
    message, description = _error_details(response)
    request_id = response.headers.get(REQUEST_ID_HEADER)
    status = response.status_code
    summary = f"Quickbase returned {status} for {method} {path}"
    detail = ": ".join(part for part in (message, description) if part)
    if detail:
        summary += f": {detail}"
    if request_id:
        summary += f" (request ID: {request_id})"
    common = {
        "status_code": status,
        "method": method,
        "path": path,
        "message": message,
        "description": description,
        "request_id": request_id,
    }
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        return RateLimitError(summary, retry_after=retry_after, **common)
    error_type = ServerError if status >= 500 else _ERRORS_BY_STATUS.get(status, APIError)
    return error_type(summary, **common)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def parse_response(response: httpx.Response, method: str, path: str) -> Any:
    """Return the decoded JSON body, None for an empty body, or raise the matching error."""
    if not response.is_success:
        raise error_from_response(response, method, path)
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise InvalidResponseError(
            f"Quickbase returned a non-JSON body for {method} {path}."
        ) from exc


def error_from_transport(exc: httpx.TransportError, method: str, path: str) -> NetworkError:
    reason = type(exc).__name__
    if isinstance(exc, httpx.TimeoutException):
        return RequestTimeoutError(f"{method} {path} timed out ({reason}).")
    return NetworkError(f"Could not complete {method} {path} ({reason}).")
