"""When to retry and how long to wait. Quickbase documents retrying only on HTTP 429.

Connection failures are also retried, because the request never reached the server. A read is
also retried after a timeout, a dropped connection, or a 502, 503, or 504, as sending it again
changes nothing. A write isn't, because it may already have been applied.
"""

import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .errors import ConfigurationError

_SECONDS = re.compile(r"\d+(\.\d+)?")
_MAX_EXPONENT = 32  # keeps 2**attempt from overflowing a float


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    """How to retry rate limits, connection failures, and reads that failed on the way.

    Args:
        max_retries: Retries after the first attempt. Use 0 to disable retrying.
        base_delay: Starting backoff in seconds, when Quickbase doesn't specify a wait or says to
            wait 0 seconds.
        max_delay: Longest backoff in seconds.
        max_retry_after: Raise `RateLimitError` rather than obey a longer `Retry-After`.
    """

    max_retries: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_retry_after: float = 60.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ConfigurationError("max_retries can't be negative.")
        if self.base_delay < 0 or self.max_delay < 0 or self.max_retry_after < 0:
            raise ConfigurationError("Retry delays can't be negative.")

    def backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter, so parallel clients don't retry in lockstep.

        Args:
            attempt: Which retry this is, starting from 0.

        Returns:
            The seconds to wait.
        """
        ceiling = min(self.max_delay, self.base_delay * 2 ** min(attempt, _MAX_EXPONENT))
        return random.uniform(ceiling / 2, ceiling)

    def rate_limit_delay(self, attempt: int, retry_after: float | None) -> float | None:
        """Seconds to wait after a 429, or None if the wait is too long to be worth it.

        Quickbase can send a wait of 0 while it's still limiting, so an immediate retry could use up
        the retries, and that's treated like no wait at all.

        Args:
            attempt: Which retry this is, starting from 0.
            retry_after: The wait Quickbase asked for, in seconds, if it did.

        Returns:
            The seconds to wait, or None to stop retrying and raise `RateLimitError`.
        """
        if not retry_after:
            return self.backoff(attempt)
        return retry_after if retry_after <= self.max_retry_after else None


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a `Retry-After` header given as seconds or an HTTP date."""
    if value is None:
        return None
    value = value.strip()
    if _SECONDS.fullmatch(value):
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())
