# Handling errors

Everything raised by this library derives from `QuickbaseError`.

```python
from pyquickbase import AuthenticationError, QuickbaseError, RateLimitError

try:
    app = qb.request("GET", "/apps/bq1234567")
except AuthenticationError:
    app = None
except RateLimitError as error:
    print(f"Still rate limited. Try again in {error.retry_after} seconds.")
except QuickbaseError as error:
    print(error)
```

## Mistakes caught before sending

These mean nothing was sent to Quickbase.

| Exception | Cause |
|---|---|
| `ConfigurationError` | Invalid client settings, or missing environment variables |
| `QueryError` | A query that can't be sent as written |
| `InvalidValueError` | A row, value, or file request that can't be sent |
| `SchemaMismatchError` | A generated class no longer matches its table |
| `UnknownTableError` | No table with that name or ID in this app |
| `UnknownFieldError` | No field with that label or ID in this table |
| `UnknownReportError` | No report with that name or ID on this table, or several with that name |
| `UnsupportedReportError` | A report that can't be read as records, such as a summary or chart |
| `ReadOnlyError` | The client isn't allowed to send this request |

## Errors from Quickbase

These derive from `APIError`, which has `status_code`, `method`, `path`, `message`, `description`,
and `request_id`. Include `request_id` when reporting a problem to Quickbase support.

| Exception | Status |
|---|---|
| `BadRequestError` | 400 |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `RateLimitError` | 429, with `retry_after` |
| `ServerError` | 5xx |

!!! note "A missing app is a 401"
    Quickbase answers with a 401 rather than a 404 for an app or table that doesn't exist, so
    `AuthenticationError` can mean either a bad token or a wrong ID. A bad realm or app can also
    return a 400.

## Other failures

| Exception | Cause |
|---|---|
| `WriteError` | Part of a write failed, and the rest may have been applied. See [When rows fail](writing.md#when-rows-fail) |
| `NetworkError` | The request failed before a response arrived |
| `RequestTimeoutError` | The request timed out. A write may still have been applied |
| `InvalidResponseError` | Quickbase reported success, but the response wasn't in the expected format |

## Retries

Rate limits and connection failures are retried automatically, honoring `Retry-After`. Nothing else
is, because a failed write may already have been applied. See
[Configuring clients](clients.md#retries) to change this.
