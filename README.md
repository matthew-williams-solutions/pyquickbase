# pyquickbase

An unofficial, typed Python SDK for the [Quickbase](https://www.quickbase.com/) API, with matching
sync and async clients.

> **Status: early alpha.** Reading data works, but writing isn't built yet, and anything may change
> before 1.0. This project is not affiliated with or endorsed by Quickbase, Inc.

## What works today

- Sync and async clients with the same API, and support for asyncio and Trio
- Field labels instead of numeric IDs, and table names instead of table IDs
- A Python query builder that escapes values, and checks for hand-written queries that Quickbase would
  otherwise accept and answer incorrectly
- Automatic paging, and records with real `date` and `datetime` values
- Automatic retries for rate limits, honoring `Retry-After`, and for connection failures
- Specific exceptions that include Quickbase's error message and request ID
- Tokens kept out of `repr`s, exceptions, and logs, and never sent to other hosts or over plain HTTP
- `qb.request()` for calling any Quickbase endpoint directly

## Coming next

Creating, updating, and deleting records, with per-record error reporting.

## Usage

The package isn't on PyPI yet. To try it, install from a clone with `uv sync`.

```python
from datetime import date
from pyquickbase import F, Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    overdue = (F("Status") != "Done") & (F("Due Date") < date.today())
    for task in tasks.query(where=overdue, select=["Title", "Due Date"], order_by="-Due Date"):
        print(task.id, task["Title"], task["Due Date"])

    print(tasks.count(F("Status") == "Done"))
```

The async client has the same API:

```python
from pyquickbase import AsyncQuickbase, F

async with AsyncQuickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    async for task in tasks.query(where=F("Status") == "Done"):
        print(task["Title"])
```

`Quickbase.from_env()` reads `QB_REALM` and `QB_USER_TOKEN` from the environment instead.

## Writing queries

Build conditions with `F`, combining them with `&` and `|` and wrapping each in parentheses.

| Python | Matches |
|---|---|
| `F("Status") == "Done"`, `!=` | Equal, not equal, ignoring case |
| `F("Hours") > 10`, `>=`, `<`, `<=` | Numbers and dates |
| `F("Notes") == None` | Empty fields |
| `F("Title").contains("x")`, `.not_contains()` | Text anywhere in the value |
| `F("Title").starts_with("x")`, `.not_starts_with()` | Text at the start of the value |
| `F("Status").is_in(["Done", "Blocked"])` | Any of the values |
| `F("Tags").op("HAS", "x")` | Any other Quickbase operator |

Use `F` for any text you don't control. It escapes quotes and rejects values containing `{` or `}`,
which Quickbase can't search for and which could otherwise change the meaning of the query.

You can also pass a Quickbase query string as `where`, using field IDs:
`"({7.EX.'Done'}OR{7.EX.'Blocked'})AND{9.BF.'today'}"`. The SDK raises `QueryError` before sending a
string with a mistake that Quickbase would silently accept, such as a lowercase operator, which it
treats as "contains", or an unknown field, which matches nothing.

## Reading records

`query()` fetches further pages as you iterate, and `limit` stops it early. Read values by label or
ID, with `record["Status"]` or `record[7]`. `dict(record)` gives you `{label: value}`. Record ID# is
always selected, and without `order_by`, records come back in Record ID# order so that pages stay
stable. For one page at a time, use `query_page()`, which returns the records, the `total` number of
matches, `has_more`, and `next_skip`.

Table and field metadata is cached for five minutes. Set `schema_ttl` on the client to change this,
or call `qb.clear_schema_cache()` after changing a table.

## Handling errors

Every exception derives from `QuickbaseError`.

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

Quickbase answers with a 401, not a 404, for an app or table that doesn't exist, so
`AuthenticationError` can mean either a bad token or a wrong ID.

Only rate limits and connection failures are retried. A timed-out write isn't retried because
Quickbase may already have applied it, so check the data before trying again.

## Customizing

Adjust retries with `RetryPolicy`. For proxies, custom TLS, logging hooks, or custom transports,
pass your own [httpx](https://www.python-httpx.org/) client, which you remain responsible for
closing.

```python
import httpx
from pyquickbase import Quickbase, RetryPolicy

http_client = httpx.Client(proxy="http://proxy.internal:8080")
qb = Quickbase(
    realm="acme",
    token="your_user_token",
    retry=RetryPolicy(max_retries=2),
    user_agent="my-app/1.0",
    http_client=http_client,
)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
