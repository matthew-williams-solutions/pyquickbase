"""Exceptions raised by pyquickbase. Everything here derives from `QuickbaseError`."""

from typing import Any, Literal


class QuickbaseError(Exception):
    """Base class for every exception this library defines."""

    # Set when the SDK knows the failed call still changed something, for `tool_error()`.
    _changed: bool = False


class ConfigurationError(QuickbaseError, ValueError):
    """The client was given invalid or missing settings."""


class NetworkError(QuickbaseError):
    """The request failed before a response was received."""


class RequestTimeoutError(NetworkError):
    """The request timed out. A timed-out write may still have been applied."""


class InvalidResponseError(QuickbaseError):
    """Quickbase reported success, but the response body wasn't in the expected format."""


class PolicyRefusedError(QuickbaseError):
    """The client's `policy` doesn't allow this request, so it wasn't sent.

    The message says what was refused, by name, which rule refused it, and what would allow it,
    without repeating any values from the request. Quickbase's own permissions still apply to
    whatever the policy allows. A field the policy hides raises `UnknownFieldError` instead, as if
    it didn't exist.

    Attributes:
        decision: The `Decision`, with the rule, the reason, and the tables and fields involved.
    """

    def __init__(self, decision: Any) -> None:
        super().__init__(decision.message())
        self.decision = decision


class CodePageChangedError(QuickbaseError):
    """A code page isn't the version the caller expected, so nothing was written.

    Read the page again, and decide from what's there now.
    """


class QueryError(QuickbaseError, ValueError):
    """A query can't be sent as written, so it wasn't sent to Quickbase."""


class TooManyRecordsError(QueryError):
    """More records match a summary than its `max_records_scanned` lets it read. None were read.

    A summary that only counts reads no records, so this is raised only for one that groups,
    totals, averages, or finds the lowest or highest value.

    Attributes:
        matched: How many records match.
        limit: The most records the summary may read.
    """

    def __init__(self, matched: int, limit: int) -> None:
        super().__init__(
            f"{matched} records match, which is more than max_records_scanned of {limit}, the "
            "most this summary may read. Counting alone works at any size. To group, total, or "
            "average them, narrow the filter or raise the limit."
        )
        self.matched = matched
        self.limit = limit

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.matched, self.limit))


class ConfirmationError(QuickbaseError):
    """A change needs the confirmation its preview or plan returned, and this one is missing,
    expired, or for something else. Nothing was changed. Preview or plan again, and show the
    person the result."""


class InvalidValueError(QuickbaseError, ValueError):
    """A write can't be sent as written, so it wasn't sent to Quickbase."""


class FileTooLargeError(InvalidValueError):
    """A file is over a limit on how much of it may be read, so reading stopped.

    Attributes:
        limit: The limit it went over.
        unit: What `limit` counts: `rows`, for `max_rows`, or `bytes`, for `max_unzipped_bytes`.
    """

    def __init__(self, limit: int, unit: Literal["rows", "bytes"]) -> None:
        what = (
            f"has more than {limit} rows, the most max_rows allows"
            if unit == "rows"
            else f"unpacks to more than {limit} bytes, the most max_unzipped_bytes allows"
        )
        super().__init__(f"The file {what}. Split it, or raise the limit.")
        self.limit = limit
        self.unit = unit

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.limit, self.unit))


class WriteError(QuickbaseError):
    """Part of a write failed, and the rest may have been applied.

    When a later batch of a large write fails, `__cause__` holds that batch's own error, such as a
    `RateLimitError` or `RequestTimeoutError`.

    Attributes:
        result: A `WriteResult` with the records that were written and the rows that failed, when
            Quickbase reported them.
    """

    def __init__(self, summary: str, *, result: Any = None) -> None:
        super().__init__(summary)
        self.result = result


class SchemaChangeError(WriteError):
    """A change in a schema plan failed, so the ones after it weren't attempted.

    Quickbase can't undo the changes before it, so `result` says which were made. Planning the
    same changes again shows only what's left, as the ones made count as already done.

    Attributes:
        result: A `SchemaChangeResult`.
    """


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

    An `ensure_` method found a field that isn't what it was asked for, or the schema changed since
    a plan was made.
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
        quickbase_code: The numeric error code Quickbase reported, on the methods that get one,
            such as `apps()`. None otherwise.
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
        quickbase_code: int | None = None,
    ) -> None:
        super().__init__(summary)
        self.status_code = status_code
        self.method = method
        self.path = path
        self.message = message
        self.description = description
        self.request_id = request_id
        self.quickbase_code = quickbase_code


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


class UnsupportedOperationError(APIError):
    """Quickbase says this operation doesn't exist.

    The methods that use Quickbase's older API raise it if Quickbase retires the call behind them.
    """


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
