"""Exceptions raised by pyquickbase. Everything here derives from `QuickbaseError`."""

from typing import Any


class QuickbaseError(Exception):
    """Base class for every exception this library defines."""


class ConfigurationError(QuickbaseError, ValueError):
    """The client was given invalid or missing settings."""


class NetworkError(QuickbaseError):
    """The request failed before a response was received."""


class RequestTimeoutError(NetworkError):
    """The request timed out. A timed-out write may still have been applied."""


class InvalidResponseError(QuickbaseError):
    """Quickbase reported success, but the response body wasn't in the expected format."""


class ReadOnlyError(QuickbaseError):
    """The client is read-only, and this request could change data. Nothing was sent."""


class QueryError(QuickbaseError, ValueError):
    """A query can't be sent as written. Nothing was sent to Quickbase."""


class InvalidValueError(QuickbaseError, ValueError):
    """A write can't be sent as written. Nothing was sent to Quickbase."""


class WriteError(QuickbaseError):
    """Part of a write failed, and the rest may have been applied.

    `result` is a `WriteResult` with the records that were written and the rows that failed, when
    Quickbase reported them.
    """

    def __init__(self, summary: str, *, result: Any = None) -> None:
        super().__init__(summary)
        self.result = result


class UnknownTableError(QuickbaseError, LookupError):
    """The app has no table with that name or ID."""


class UnknownFieldError(QuickbaseError, LookupError):
    """The table has no field with that label or ID."""


class UnknownReportError(QuickbaseError, LookupError):
    """The table has no report with that name or ID, or several reports share the name."""


class UnsupportedReportError(QuickbaseError):
    """The report can't be read as records, such as a summary or chart report."""


class SchemaMismatchError(QuickbaseError):
    """A generated class no longer matches its table, so it needs generating again."""


class APIError(QuickbaseError):
    """Quickbase answered with an unsuccessful status code.

    `message` and `description` are Quickbase's own wording, when it sent any.
    Include `request_id` when reporting a problem to Quickbase support.
    """

    def __init__(
        self,
        summary: str,
        *,
        status_code: int = 0,
        method: str = "",
        path: str = "",
        message: str | None = None,
        description: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.status_code = status_code
        self.method = method
        self.path = path
        self.message = message
        self.description = description
        self.request_id = request_id


class BadRequestError(APIError):
    """400: Quickbase rejected the request as malformed."""


class AuthenticationError(APIError):
    """401: the token is missing, invalid, or expired."""


class PermissionDeniedError(APIError):
    """403: the token's user may not perform this action."""


class NotFoundError(APIError):
    """404: the app, table, field, or record does not exist."""


class RateLimitError(APIError):
    """429: retries ran out, or Quickbase asked for a longer wait than the retry policy allows."""

    def __init__(self, summary: str, *, retry_after: float | None = None, **kwargs: object) -> None:
        super().__init__(summary, **kwargs)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        self.retry_after = retry_after


class ServerError(APIError):
    """5xx: Quickbase failed to process the request."""
