<p align="center"><img src="https://raw.githubusercontent.com/matthew-williams-solutions/pyquickbase/main/assets/readme-banner.png" alt="pyquickbase" width="640"></p>

An unofficial, typed Python SDK for the [Quickbase](https://www.quickbase.com/) API, with matching
sync and async clients.

**[Read the documentation](https://matthew-williams-solutions.github.io/pyquickbase/)**

> **Status: release candidate.** Small changes are still possible before 1.0. This project is not affiliated with or endorsed by Quickbase, Inc.

## What works today

- Sync and async clients with the same API, and support for asyncio and Trio
- Field labels instead of numeric IDs, and table names instead of table IDs
- A Python query builder that escapes values, and checks for hand-written queries that Quickbase would
  otherwise accept and answer incorrectly
- Automatic paging, and records with real `date` and `datetime` values
- Automatic retries for rate limits, honoring `Retry-After`, for connection failures, and for reads
  that time out or get a 502, 503, or 504
- Specific exceptions that include Quickbase's error message and request ID
- Tokens kept out of `repr`s, exceptions, and logs, and never sent to other hosts or over plain HTTP
- Creating, updating, and deleting records, with errors reported for each failed row
- Creating and changing tables, fields, and relationships
- Running table reports, with automatic paging
- Uploading, downloading, and deleting file attachments
- Running formulas, and creating formula fields
- `pyquickbase codegen`, which generates typed classes from your tables
- `qb.request()` for calling any Quickbase endpoint directly

## Usage

```bash
pip install pyquickbase==1.0.0rc10
```

This is a release candidate, so pin the exact version.

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

Use `F` for any text you don't control. Quickbase reads some text as query syntax even inside quotes,
so `F` escapes quotes, refuses values containing `{` or `}` or like `_FID_6`, and sends a standalone
`OR` or `AND` in lowercase so that it's matched as text.

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

## Writing records

Rows are dictionaries keyed by field label or ID. Python dates, timezone-aware datetimes, and
`Decimal` values are converted for you, and `None` clears a field.

| Method | What it does |
|---|---|
| `table.insert(rows)` | Creates records, and never changes existing ones |
| `table.update(rows)` | Changes records. Each row needs a `Record ID#`, and a missing record is an error |
| `table.upsert(rows, merge_on="Code")` | Updates the record whose unique field matches, or creates one |
| `table.delete(where)` | Deletes matching records and returns the count. `where` is required |

```python
from datetime import date
from pyquickbase import F, WriteError

result = tasks.insert([{"Title": "Pour slab", "Status": "Not Started", "Due Date": date.today()}])
print(result.created_ids)

tasks.update([{"Record ID#": 7, "Status": "Done", "Notes": None}])
tasks.delete(F("Status") == "Cancelled")

try:
    tasks.insert(rows)
except WriteError as error:
    print(error.result.created_ids)  # these were written
    for failed in error.result.errors:
        print(rows[failed.index], failed.messages)
```

Quickbase writes the rows it can and reports the rest, so a `WriteError` doesn't mean nothing changed.
Large writes are split into batches of 1,000 rows, which you can change with `batch_size`. Check
`error.result` before trying again.

## Changing the schema

```python
projects = app.create_table("Projects", single_record_name="Project")
projects.create_field("Name", "text")
projects.create_field("Status", "text-multiple-choice", choices=["Active", "Done"])
projects.create_field("Code", "text", unique=True)

tasks.create_relationship(projects, foreign_key_label="Project", lookup_fields=["Name"])
tasks.update_field("Notes", label="Comments")
tasks.delete_fields("Old Field")
tasks.drop(confirm="Tasks")  # deletes the table and its records
```

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

Rate limits and connection failures are retried. Reads, meaning GET requests, record queries, report runs, and formula runs, are also
retried after a timeout, a dropped connection, or a 502, 503, or 504. A write isn't retried after
those, because Quickbase may already have applied it, so check the data before trying again.

## Limiting what a client can do

Pass `read_only=True` to guarantee a client can't change data. It only sends GET requests, record
queries, report runs, and formula runs, including through `qb.request()`, and raises `WriteRefusedError`
for anything else without sending it, though a table method may read the table's metadata first.
Running a report adds to its usage count in Quickbase, but changes no records.

To limit writes by table, action, and field, such as for an AI agent, pass `WriteRules`. To limit reads
by table, field, report, and number of records, pass `ReadRules`, which also leaves out of each
response what it doesn't allow. Anything they don't list is refused. See [Limiting what a client can do](https://matthew-williams-solutions.github.io/pyquickbase/guides/clients/#limiting-what-a-client-can-do)
for these, your own policies, and what these limits don't do.

```python
from pyquickbase import Quickbase, ReadAccess, ReadRules, WriteAccess, WriteRules

writes = WriteRules({"bqtasks123": WriteAccess(insert=True, update=[7, 12])})
reads = ReadRules({"bqtasks123": ReadAccess(fields=[6, 7, 12], max_records=50)})
qb = Quickbase(realm="acme", token="your_user_token", write_policy=writes, read_policy=reads)
```

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
