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


class RequestRefusedError(QuickbaseError):
    """The client's own limits refused this request, so it wasn't sent.

    Catch this for either kind. `WriteRefusedError` and `ReadRefusedError` say which limit it was.
    The message says what was refused and why, without repeating any values from the request.
    """


class WriteRefusedError(RequestRefusedError):
    """The client's limits on writes don't allow this request, which could change data.

    `read_only`, `allowed_writes`, and `write_policy` all raise this. Nothing was sent.
    """


class ReadRefusedError(RequestRefusedError):
    """The client's `read_policy` doesn't allow this read, so it wasn't sent.

    It's raised for a field, table, report, or number of records that the policy doesn't allow.
    Quickbase's own permissions still apply to whatever it allows.
    """


class QueryError(QuickbaseError, ValueError):
    """A query can't be sent as written, so it wasn't sent to Quickbase."""


class InvalidValueError(QuickbaseError, ValueError):
    """A write can't be sent as written, so it wasn't sent to Quickbase."""


class WriteError(QuickbaseError):
    """Part of a write failed, and the rest may have been applied.

    Attributes:
        result: A `WriteResult` with the records that were written and the rows that failed, when
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
    """A table doesn't match what the code expects, so nothing was changed.

    A generated class no longer matches its table, so it needs generating again, or an `ensure_`
    method found a field that isn't what it was asked for.
    """


class APIError(QuickbaseError):
    """Quickbase answered with an unsuccessful status code.

    This is the base of the errors for each status, so it's what you catch for any of them.

    Attributes:
        status_code: The HTTP status.
        method: The request's HTTP method.
        path: The request's path.
        message: Quickbase's wording, when it sent any. It can contain HTML, so escape it before
            showing it in a web page.
        description: Quickbase's further explanation, when it sent any.
        request_id: Include this when reporting a problem to Quickbase support.
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
    """401: the token is invalid, or the app or table doesn't exist or is out of the token's reach.

    Quickbase gives the same answer for all of these, so this can't say which one happened.
    """


class PermissionDeniedError(APIError):
    """403: the token's user may not perform this action."""


class NotFoundError(APIError):
    """404: what was asked for doesn't exist. An app that doesn't exist is a 401 instead."""


class RateLimitError(APIError):
    """429: retries ran out, or Quickbase asked for a longer wait than the retry policy allows.

    Attributes:
        retry_after: The wait Quickbase asked for, in seconds, if it did.
    """

    def __init__(self, summary: str, *, retry_after: float | None = None, **kwargs: object) -> None:
        super().__init__(summary, **kwargs)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        self.retry_after = retry_after


class ServerError(APIError):
    """5xx: Quickbase failed to process the request."""


class WritePermissionError(WriteError, PermissionDeniedError):
    """A write was refused because of the token's role, so nothing in the failed rows changed.

    Quickbase refuses some writes with a 403, and reports others as row errors in a successful
    response, depending on the role. Both raise this, so `except PermissionDeniedError` and
    `except WriteError` each catch it. `result` holds what earlier batches or rows did write.
    If some rows failed for other reasons as well, you get a plain `WriteError`, whose rows each
    have a `kind`.

    Attributes:
        status_code: 403 for a refused request, and 207 for row errors.
    """

    def __init__(
        self, summary: str, *, result: Any = None, status_code: int = 403, **kwargs: Any
    ) -> None:
        PermissionDeniedError.__init__(self, summary, status_code=status_code, **kwargs)
        self.result = result
