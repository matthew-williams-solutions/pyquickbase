# Handling errors

This page covers what the SDK raises, what each error means for your data, and what's retried for you.
Every exception this library defines derives from `QuickbaseError`, and the async client raises the same
ones.

## Catching errors

Catch the specific errors you can act on, and `QuickbaseError` for the rest.

```python
from pyquickbase import (
    AuthenticationError,
    F,
    QueryError,
    Quickbase,
    QuickbaseError,
    RateLimitError,
)

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    try:
        print(tasks.count(F("Status") == "Open"))
    except QueryError as error:
        print("Fix the query:", error)
    except AuthenticationError:
        print("Check the token, and the app and table IDs")
    except RateLimitError as error:
        print(f"Still rate limited. Try again in {error.retry_after} seconds.")
    except QuickbaseError as error:
        print(error)
```

The other examples on this page use `tasks`, a table from a client made in the same way.

Reading a missing key from a `Record` raises Python's `KeyError`, as any mapping does. That includes
`record.id` on a report row without a Record ID# column.

## Mistakes caught before sending

These mean the request wasn't sent, though the SDK may have read the table's metadata first to check it.

| Exception | Cause |
|---|---|
| `ConfigurationError` | Invalid client settings, or missing environment variables |
| `QueryError` | A query that can't be sent as written, such as a lowercase operator in a hand-written string |
| `InvalidValueError` | A row, value, or file request that can't be sent |
| `SchemaMismatchError` | A generated class no longer matches its table, or an `ensure_` method found a field that isn't what it was asked for |
| `UnknownTableError` | No table with that name or ID in this app, or the client's policy hides it |
| `UnknownFieldError` | No field with that label or ID in this table, or the token's role or the client's policy hides it |
| `UnknownReportError` | No report with that name or ID on this table, several with that name, or one the role can't use |
| `UnsupportedReportError` | A report that can't be read as records, such as a summary or chart |
| `PolicyRefusedError` | The client's [policy](clients.md#limiting-what-a-client-can-do) doesn't allow this request |

A `PolicyRefusedError`'s `decision` says what was refused, by name, which rule refused it, and what
would allow it. Its message follows one form: `Refused by <layer>: <what>. <reason>. (rule: <rule>)
Nothing was sent. To allow it: <hint>.`

```python
from pyquickbase import QueryError

try:
    tasks.delete("{7.ex.'Done'}")
except QueryError as error:
    print(error)  # Quickbase would read the lowercase operator as "contains"
```

[Try it in the playground](../playground-app/index.html#catch-mistakes), with simulated data.

## Writes that fail partway

Quickbase writes the rows it can and reports the rest, so a `WriteError` doesn't mean nothing changed.
Its `result` holds what was written and each failed row, and `result.row_ids` matches an insert's new
records to your rows.

```python
from pyquickbase import WriteError

rows = [{"Title": "Order chairs"}, {"Title": "Book venue", "Status": "Finished"}]
try:
    tasks.insert(rows)
except WriteError as error:
    for row, record_id in zip(rows, error.result.row_ids):
        print(row["Title"], record_id)  # None for a row that failed
    for failed in error.result.errors:
        print(failed.index, failed.kind, failed.messages)
```

When a later batch of a large write fails, the error's `__cause__` is that batch's own error, such as a
`RateLimitError`, and `result` holds the batches before it. See [When rows fail](writing.md#when-rows-fail)
for each row's `kind`.

## Refused writes

Quickbase refuses a write in two ways, depending on the role. A role with neither add nor modify rights on
the table gets a 403. Any other refusal, including a field the role can only view, is a row error in a
successful response. `WritePermissionError` covers both. It's a `WriteError` and a
`PermissionDeniedError`, so either one catches it, and its `status_code` is 403 or 207.

If some rows failed for another reason too, you get a plain `WriteError`, so check each row's `kind`. A row
with one field the role can't change is refused whole, so its other fields don't change either.

## Errors from Quickbase

These derive from `APIError`, which has `status_code`, `method`, `path`, `message`, `description`, and
`request_id`. Include `request_id` when reporting a problem to Quickbase support.

| Exception | Status |
|---|---|
| `BadRequestError` | 400 |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `RateLimitError` | 429, with `retry_after` |
| `ServerError` | 5xx |

!!! note "A missing app is a 401"
    Quickbase answers with a 401 rather than a 404 for an app or table that doesn't exist, or that the
    token can't reach, with the same wording it uses for a bad token. So `AuthenticationError` can mean
    any of these, and its message says to check the ID too. A bad realm or app can also return a 400.

!!! warning "Messages can contain HTML"
    Quickbase's formula error messages can include HTML, such as a `javascript:` link, and errors pass
    them on unchanged. Escape them before showing them in a web page.

## When a write may have landed

These errors don't say whether Quickbase applied a write, so check the data before trying again.

| Exception | Cause |
|---|---|
| `NetworkError` | The request failed before a response arrived |
| `RequestTimeoutError` | The request timed out, a kind of `NetworkError` |
| `ServerError` | Quickbase failed while handling it |
| `InvalidResponseError` | Quickbase reported success, but the response wasn't in the expected format |

## Retries

The client retries these for you, so an error you catch means the retries ran out.

| Failure | Reads | Writes |
|---|---|---|
| 429, rate limited, honoring `Retry-After` | Yes | Yes |
| Couldn't connect | Yes | Yes, as nothing was sent |
| A timeout or dropped connection after sending | Yes | No, as it may have been applied |
| 502, 503, or 504 | Yes | No |

Reads are GET requests, record queries, report runs, and formula runs. A long burst of requests can still
use up a call's retries and raise `RateLimitError`, so handle it if you make many calls at once. See
[Configuring clients](clients.md#retries) to change the retries.

## Errors for a model

`tool_error()` turns any of these into plain data with a fixed `code`, whether anything `changed`, and
whether it's `retryable`. See [Returning results to a model](agents.md#returning-results-to-a-model).
