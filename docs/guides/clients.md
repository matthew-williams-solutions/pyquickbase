# Configuring clients

`Quickbase` and `AsyncQuickbase` take the same options. Use them as context managers so that
connections get closed.

## Sync or async

The clients have the same methods, and the sync client is a true sync client rather than a wrapper
around asyncio. That means it works in notebooks, in threads, and inside a running event loop.
One sync client can be shared across threads. The async client works with both asyncio and Trio.

[Try it in the playground](../playground-app/index.html#use-the-async-client), with simulated data.

## Using several tokens

`Quickbase.from_env()` reads `QB_REALM` and `QB_USER_TOKEN`. A program with more than one token can
name the variable, such as a setup token and a restricted one:

```python
qb = Quickbase.from_env(token_env="QB_SERVICE_TOKEN")
```

## Limiting what a client can do

Choose the narrowest option that fits. A refused write raises `WriteRefusedError`, and a refused read
raises `ReadRefusedError`, both without sending anything, though a table method may read the table's
metadata first. Both are a `RequestRefusedError`. They also apply to `qb.request()`.

| Option | What the client can do | Use it for |
|---|---|---|
| `read_only=True` | Write nothing | Reports, dashboards, and anything that only looks |
| `write_policy=WriteRules(...)` | Write only the tables, actions, and fields you list | An AI agent or a job with a narrow purpose |
| `write_policy=your_function` | Write whatever your function allows | Rules that the list can't express |
| `read_policy=ReadRules(...)` | Read only the tables, fields, reports, and number of records you list | An AI agent that shouldn't see everything the token can |
| `read_policy=your_function` | Read whatever your function allows | Logging reads, or limits the list can't express |

A read policy works with any of the others. These protect against bugs in your code and mistakes by a
model. See [what they don't do](#what-these-limits-dont-do), and give your token access to as little
as possible, so that Quickbase enforces the limit too.

### Making a client read-only

`read_only=True` guarantees a client can't change data. It only sends GET requests, record queries,
report runs, and formula runs. Running a report adds to its usage count in Quickbase, but changes no
records.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token", read_only=True) as qb:
    print(qb.app("bq1234567").tables())
```

### Limiting writes by table, action, and field

Pass `WriteRules`, which lists what's allowed on each table. Anything not listed is refused, including
any other table, so a table added later starts out blocked.

Rules use table and field IDs, because a name or label can be renamed, which could leave a rule
protecting the wrong thing. Look them up once when you set up, then either keep them in your settings
or use them right away, as this does.

```python
from pyquickbase import WriteAccess, Quickbase, WriteRules

with Quickbase(realm="acme", token="your_user_token") as setup:
    tasks = setup.app("bq1234567").table("Tasks")
    tasks_id = tasks.info().id
    status, notes = tasks.resolve_field_ids(["Status", "Notes"])

rules = WriteRules({tasks_id: WriteAccess(insert=True, update=[status, notes])})

with Quickbase(realm="acme", token="your_user_token", write_policy=rules) as qb:
    tasks = qb.app("bq1234567").table(tasks_id)
    created = tasks.insert([{"Title": "Order chairs"}]).created_ids[0]
    tasks.update([{"Record ID#": created, "Status": "Done"}])  # Allowed
    # tasks.update([{"Record ID#": created, "Title": "Changed"}])  # Raises WriteRefusedError
    # tasks.delete(F("Status") == "Done")  # Raises WriteRefusedError
```

`WriteAccess` grants nothing unless you say so:

| Argument | What it grants | Values |
|---|---|---|
| `insert` | Creating records | `True`, or a list of the field IDs it may set |
| `update` | Changing records | `True`, or a list of the field IDs it may set |
| `delete` | Deleting records | `True`. Field lists don't limit this, as deleting a record removes all of its fields |
| `delete_files` | Deleting file versions | `True` |

Things to know:

- **An `upsert()` needs both `insert` and `update`**, because it can do either, and a field list on
  either one needs to include every field, including the `merge_on` field.
- **`update()` doesn't need `Record ID#` in its list**, because that only says which record to change.
- **A file upload counts as setting its field.**
- **Only record and file writes can be allowed.** Changes to tables, fields, and relationships are all
  refused, since those can affect other tables, and some of them don't say which. To allow them, write
  your own policy, or use a separate client for setup.
- **`insert` alone can't be limited to creating records on a table with a custom key field.** If someone
  has changed a table's key field in the Quickbase UI, a row with an existing value in that field
  [changes that record](../api-notes.md#writes). `table.insert()` refuses those tables, so use `upsert()`,
  which needs both `insert` and `update`.
- **`qb.request()` can't look up a table's key field.** So any write through it without a merge field
  needs both `insert` and `update`.
- **The key field is read from the [metadata cache](schema.md#the-metadata-cache).** If it might change
  while a client is running, pass `schema_ttl=0`.

### Writing your own policy

A policy is a function that takes a `WriteRequest` and returns `True` to allow the write. Anything else
refuses it, including `False`, `None`, a truthy value, or an error your function raises, so a bug can't
let a write through. To give a reason, raise `WriteRefusedError` with a message of your own.

```python
from pyquickbase import Quickbase, WriteRefusedError, WriteRequest


def small_batches_only(request: WriteRequest) -> bool:
    if request.row_count is None or request.row_count > 100:
        raise WriteRefusedError("This job only writes up to 100 records at a time.")
    return True


qb = Quickbase(realm="acme", token="your_user_token", write_policy=small_batches_only)
```

A `WriteRequest` says what the write would do. It holds no values, so it's safe to log.

| Attribute | What it holds |
|---|---|
| `action` | `insert`, `update`, `upsert`, `delete`, `delete_file`, or `other` |
| `table_id` | The table's ID |
| `field_ids` | The IDs of the fields it sets |
| `row_count` | How many rows are in this request |
| `method`, `path` | The HTTP request, for anything `action` doesn't cover |

**Treat `None` as "couldn't be determined", not as "nothing".** For example, `field_ids` is `None` if a
row writes a field ID in an unusual form, such as `07`, which Quickbase reads as field 7, and
`row_count` is `None` for a delete, since a filter doesn't say how many records it matches. In the
example above, that's why a delete is refused.

Have your policy refuse an `action` it doesn't recognize, as a later version can add one.

`upsert` means a write that may do either, which isn't only `table.upsert()`. Any merge on a field other
than `Record ID#` is one, because Quickbase creates a record when the value doesn't match. So is a write
without a merge field that goes through `qb.request()` or to a table with a different key field, because
it can change an existing record.

A policy has to be a plain function, even with the async client, as it runs before each request is
sent, and shouldn't make network calls. It can't change a request, only allow or refuse it. It runs once
per request, so a write split into batches is checked batch by batch, and it can see each batch's own
`row_count`, not the total.

A custom policy is only as careful as its author, and returning `True` too often removes the protection,
so use `WriteRules` when it can express what you need.

### Combining limits

`all_of()` requires a write to pass every policy, in order, and stops at the first refusal.

```python
from pyquickbase import WriteAccess, Quickbase, WriteRules, WriteRequest, all_of


def log_write(request: WriteRequest) -> bool:
    print(request.action, request.table_id, request.row_count)
    return True


rules = WriteRules({"bqtasks123": WriteAccess(insert=True)})
qb = Quickbase(realm="acme", token="your_user_token", write_policy=all_of(rules, log_write))
```

Put `WriteRules` first if a later policy records what was allowed.

To limit a client to exact requests on any table, check `method` and `path` in your own policy.
Passing `read_only=True` with a write policy is a `ConfigurationError`, as it would refuse everything
the policy allows.

```python
from pyquickbase import Quickbase, WriteRequest


def record_writes_only(request: WriteRequest) -> bool:
    return (request.method, request.path) == ("POST", "/records")


qb = Quickbase(realm="acme", token="your_user_token", write_policy=record_writes_only)
```

### Limiting reads by table, field, and number of records

Pass `ReadRules`, which lists what may be read from each table. As with writes, anything not listed is
refused, and rules use IDs.

```python
from pyquickbase import Quickbase, ReadAccess, ReadRules

with Quickbase(realm="acme", token="your_user_token") as setup:
    visits = setup.app("bq1234567").table("Appointments")
    visits_id = visits.info().id
    when, status, technician = visits.resolve_field_ids(["When", "Status", "Technician"])

# It can read these three fields, filter on Status and Technician, and get 50 records at a time.
access = ReadAccess(fields=[when, status, technician], filter=[status, technician], max_records=50)
agent_client = Quickbase(
    realm="acme",
    token="your_user_token",
    read_policy=ReadRules({visits_id: access}),
    read_only=True,
)
```

A table or field that isn't listed acts as if the token couldn't see it, as a role that hides it does.
`app.tables()` and `table.fields()` leave it out, a query by default returns only the fields it may, and
naming one raises `UnknownTableError` or `UnknownFieldError`. Asking for more than is allowed, such as
filtering on a field that's only returned, or 200 records, raises `ReadRefusedError`, which says what to
change.

`ReadAccess` grants nothing beyond `fields` unless you say so:

| Argument | What it grants | Default |
|---|---|---|
| `fields` | Returning these fields. `True` means every field, including ones added later | Required |
| `filter` | Filtering on these fields | The same as `fields` |
| `sort` | Sorting by these fields | The same as `fields` |
| `write_only` | Naming these fields in a write, without reading them | None |
| `files` | Downloading from these file fields. Listing one in `fields` only returns the file's name and version details | None |
| `reports` | Running these table reports, by ID. Columns outside `fields` are left out | None |
| `summary_reports` | Running these summary and chart reports, by ID, with all of their columns | None |
| `max_records` | How many records one query or report can reach, counting `skip` | 100 |
| `max_summary_records` | How many records `summarize()` may read for one summary | 10,000 |
| `user_emails` | Email addresses in user values, and the uploader's details in file values | Left out |

Things to know:

- **`Record ID#` is always allowed,** because the SDK uses it to page through records and to find them.
- **`max_records` limits each query, not the client.** A query needs a `limit`, or a `top`, of at most
  this, and it's refused rather than cut short, so the result is never partly missing without saying so.
  A model can still reach other records with another filter. To limit how many a client reads in all,
  count them in your own policy.
- **A field in `filter` or `sort` reveals something about its values,** even if it isn't returned,
  because a model can see which records match and in which order.
- **A listed report's own filter and sort can use any field,** and someone can change them in the
  Quickbase interface, so list only reports you've checked. A summary report's columns can't be matched to
  fields, so listing one allows all of them.
- **Metadata keeps only what the SDK reads.** A field's formula, a report's filter, and anything Quickbase
  adds later are left out of the response, and so of `raw`.
- **Some things are always refused:** running a formula, which can read other tables and fields, an app's
  details and roles, running a report through `qb.request()`, which doesn't say what kind of report it
  is, any request it doesn't recognize, and any change to tables or fields, as a new formula field could
  read whatever the rules hide. Use a separate client for those.
- **A generated class must only use allowed fields,** or `SchemaMismatchError` says a field no longer
  exists.
- **A delete is held to `filter`,** because the number deleted shows how many records matched.

### Writing your own read policy

As with writes, a read policy is a function that takes a `ReadRequest` and returns `True` to allow the
read, which holds no values, so it's safe to log. Only `ReadRules` trims what comes back, so combine
yours with it using `all_of()`, which applies every `ReadRules` inside it.

```python
from pyquickbase import Quickbase, ReadAccess, ReadRequest, ReadRules, all_of


def log_read(request: ReadRequest) -> bool:
    print(request.action, request.table_id, request.field_ids)
    return True


rules = ReadRules({"bqtasks123": ReadAccess(fields=[6, 7])})
qb = Quickbase(realm="acme", token="your_user_token", read_policy=all_of(rules, log_read))
```

| Attribute | What it holds |
|---|---|
| `action` | `query`, `summarize`, `delete`, `report`, `summary_report`, `file`, `formula`, `tables`, `fields`, `relationships`, `reports`, `app`, `roles`, or `other` |
| `table_id` | The table's ID |
| `field_ids`, `filter_field_ids`, `sort_field_ids` | The fields it returns, filters on, and sorts by |
| `top`, `skip` | How many records it asks for, and how many it passes over |
| `report_id` | The report it runs |
| `method`, `path` | The HTTP request, for anything `action` doesn't cover |

`None` means "couldn't be determined", as it does for writes. A query without `sortBy` has `None` for
`sort_field_ids`, because the table's default sort would apply, and one without a `top` has `None` there,
because Quickbase would return every record. Have your policy refuse an `action` it doesn't recognize.
A read policy sees changes to tables and fields as `other`.

### What these limits don't do

- **They can't see copies of a value.** A formula, lookup, or summary field you allow can show a field you
  don't, and an address field shows its subfields. Check what each allowed field is built from.
- **A write can reveal a value.** An update reports a record as unchanged when a field already held the
  value it was given, so a client that may write a field can test what it holds.
- **They limit this client, not the token.** Code that can create another client, or call the API
  directly, can get around them. To limit that, the token's role has to do it.
- **They don't cover what Quickbase does next.** A pipeline or automation can still change other tables
  after an allowed write.
- **They can't limit by record.** An update only says which record to change, so checking its contents
  would mean reading it first, and it could change in between. Use a role with
  [record-level rules](../api-notes.md#record-level-rules), which Quickbase enforces itself.

### When something is refused

| What happened | Exception | What's left behind |
|---|---|---|
| The table, action, or a field isn't allowed | `WriteRefusedError` | Nothing was sent |
| The policy returned anything but `True`, or raised an error | `WriteRefusedError` | Nothing was sent. An error it raised is kept as the `__cause__` |
| A later batch of a write was refused | `WriteError` | **Earlier batches were written**, and are in `error.result` |
| `WriteAccess` with an empty list, or labels instead of IDs | `ConfigurationError` | The rules weren't created |
| `WriteRules` with anything but `{table ID: WriteAccess(...)}` | `ConfigurationError` | The rules weren't created |
| A read isn't allowed, such as a field, report, or too many records | `ReadRefusedError` | Nothing was sent |
| A table or field the read rules don't list is named | `UnknownTableError` or `UnknownFieldError` | Nothing was sent |
| `ReadAccess` with labels, report names, or a limit below 1 | `ConfigurationError` | The rules weren't created |
| An async function as a policy, `read_only` with a write policy, or a policy of the wrong kind | `ConfigurationError` | The client wasn't created |

## Retries

```python
from pyquickbase import Quickbase, RetryPolicy

policy = RetryPolicy(max_retries=2, base_delay=0.5, max_delay=10, max_retry_after=30)
qb = Quickbase(realm="acme", token="your_user_token", retry=policy)
```

`max_retry_after` is the longest `Retry-After` the client will wait for. Past that, it raises
`RateLimitError`. A `Retry-After` of 0 uses the backoff from `base_delay` instead, because Quickbase
can send it while still limiting. Use `max_retries=0` to disable retrying.

What's retried:

| Failure | Reads | Writes |
|---|---|---|
| 429, rate limited | Yes | Yes |
| Couldn't connect, or no free connection in the pool | Yes | Yes, as nothing was sent |
| A timeout or dropped connection after sending | Yes | No, as it may have been applied |
| 502, 503, or 504 | Yes | No |
| Any other error status | No | No |

Reads are GET requests, record queries, report runs, and formula runs. Running a report again adds to its usage count again.

`timeout` applies to each step of a request separately, connecting, sending, and waiting for each part of
the response, as httpx does, rather than to the whole request.

## Using another API host

`base_url` sets the API root, which is `https://api.quickbase.com/v1` by default. The right host for EU
realms hasn't been confirmed, so pass `base_url` if yours differs. It must use HTTPS, except for
loopback addresses.

## Proxies, TLS, and logging

Pass your own [httpx](https://www.python-httpx.org/) client for proxies, custom TLS, event hooks, or
custom transports. You remain responsible for closing it.

```python
import httpx
from pyquickbase import Quickbase

http_client = httpx.Client(proxy="http://proxy.internal:8080")
qb = Quickbase(
    realm="acme",
    token="your_user_token",
    user_agent="my-app/1.0",
    http_client=http_client,
)
```

Tokens are kept out of `repr`s, exceptions, and logs, and the client refuses to send them over plain
HTTP, except to your own machine.

## Calling any endpoint

For endpoints without a dedicated method, `request()` returns the decoded JSON body.

```python
reports = qb.request("GET", "/reports", params={"tableId": "bq7654321"})
```
