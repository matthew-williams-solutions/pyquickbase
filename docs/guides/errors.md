# Handling errors

Every exception this library defines derives from `QuickbaseError`. Reading a missing key from a
`Record`, including `record.id` on a report row without Record ID#, raises Python's `KeyError`, as any
mapping does.

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

These mean the request wasn't sent, though the SDK may have read table metadata first to check it.

| Exception | Cause |
|---|---|
| `ConfigurationError` | Invalid client settings, or missing environment variables |
| `QueryError` | A query that can't be sent as written |
| `InvalidValueError` | A row, value, or file request that can't be sent |
| `SchemaMismatchError` | A generated class no longer matches its table, or an `ensure_` method found a field that isn't what it was asked for |
| `UnknownTableError` | No table with that name or ID in this app |
| `UnknownFieldError` | No field with that label or ID in this table, or the token's role can't see it |
| `UnknownReportError` | No report with that name or ID on this table, or several with that name |
| `UnsupportedReportError` | A report that can't be read as records, such as a summary or chart |
| `ReadOnlyError` | The client isn't allowed to send this request |

## Refused writes

Quickbase reports the same kind of refusal in two ways. A role with neither add nor modify rights on a
table gets a 403, and any other refusal is a row error in a successful response, including a field the
role can only view. `WritePermissionError` covers both, and is both a `WriteError` and a
`PermissionDeniedError`, so either one catches it. Its `status_code` is 403 or 207.

If some rows failed for another reason too, you get a plain `WriteError`. Check each row's
[`kind`](writing.md#when-rows-fail). A row with one field the role can't change is refused whole, so
its other fields don't change either.

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

!!! warning "Messages can contain HTML"
    Quickbase's formula error messages can include HTML, such as a `javascript:` link, and errors
    pass them on unchanged. Escape them before showing them in a web page.

## Other failures

| Exception | Cause |
|---|---|
| `WriteError` | Part of a write failed, and the rest may have been applied. See [When rows fail](writing.md#when-rows-fail) |
| `NetworkError` | The request failed before a response arrived |
| `RequestTimeoutError` | The request timed out. A write may still have been applied |
| `InvalidResponseError` | Quickbase reported success, but the response wasn't in the expected format |

## Retries

Rate limits and connection failures are retried automatically, honoring `Retry-After`. Nothing else
is, because a failed write may already have been applied. A long burst of requests can still use up a
call's retries and raise `RateLimitError`, so handle it if you make many calls at once. See
[Configuring clients](clients.md#retries) to change this.
