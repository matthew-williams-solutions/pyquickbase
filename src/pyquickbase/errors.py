"""Exceptions raised by pyquickbase. Everything here derives from `QuickbaseError`."""


class QuickbaseError(Exception):
    """Base class for every error raised by this library."""


class ConfigurationError(QuickbaseError, ValueError):
    """The client was given invalid or missing settings."""


class NetworkError(QuickbaseError):
    """The request failed before a response was received."""


class RequestTimeoutError(NetworkError):
    """The request timed out. A timed-out write may still have been applied."""


class InvalidResponseError(QuickbaseError):
    """Quickbase reported success, but the response body wasn't in the expected format."""


class QueryError(QuickbaseError, ValueError):
    """A query can't be sent as written. Nothing was sent to Quickbase."""


class UnknownTableError(QuickbaseError, LookupError):
    """The app has no table with that name or ID."""


class UnknownFieldError(QuickbaseError, LookupError):
    """The table has no field with that label or ID."""


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
