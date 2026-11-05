# Configuring clients

`Quickbase` and `AsyncQuickbase` take the same options. Use them as context managers so that
connections get closed.

## Sync or async

The clients have the same methods, and the sync client is a true sync client rather than a wrapper
around asyncio. That means it works in notebooks, in threads, and inside a running event loop.
One sync client can be shared across threads. The async client works with both asyncio and Trio.

## Using several tokens

`Quickbase.from_env()` reads `QB_REALM` and `QB_USER_TOKEN`. A program with more than one token can
name the variable, such as a setup token and a restricted one:

```python
qb = Quickbase.from_env(token_env="QB_SERVICE_TOKEN")
```

## Limiting what a client can do

Choose the narrowest option that fits. Each one raises `ReadOnlyError` for anything it doesn't allow,
without sending it, though a table method may read the table's metadata first. They also apply to
`qb.request()`.

| Option | What the client can write | Use it for |
|---|---|---|
| `read_only=True` | Nothing | Reports, dashboards, and anything that only looks |
| `write_policy=TableRules(...)` | Only the tables, actions, and fields you list | An AI agent or a job with a narrow purpose |
| `write_policy=your_function` | Whatever your function allows | Rules that the list can't express |
| `allowed_writes={...}` | Only the exact requests you list, on any table | Limiting a client to a kind of request |

None of them limit reads, other than which requests count as one. These protect against bugs in your
code and mistakes by a model. See [what they don't do](#what-these-limits-dont-do), and give your
token access to as little as possible, so that Quickbase enforces the limit too.

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

Pass `TableRules`, which lists what's allowed on each table. Anything not listed is refused, including
any other table, so a table added later starts out blocked.

Rules use table and field IDs, because a name or label can be renamed, which could leave a rule
protecting the wrong thing. Look them up once when you set up, then either keep them in your settings
or use them right away, as this does.

```python
from pyquickbase import Allow, Quickbase, TableRules

with Quickbase(realm="acme", token="your_user_token") as setup:
    tasks = setup.app("bq1234567").table("Tasks")
    tasks_id = tasks.info().id
    status, notes = tasks.resolve_field_ids(["Status", "Notes"])

rules = TableRules({tasks_id: Allow(insert=True, update=[status, notes])})

with Quickbase(realm="acme", token="your_user_token", write_policy=rules) as qb:
    tasks = qb.app("bq1234567").table(tasks_id)
    created = tasks.insert([{"Title": "Order chairs"}]).created_ids[0]
    tasks.update([{"Record ID#": created, "Status": "Done"}])  # Allowed
    # tasks.update([{"Record ID#": created, "Title": "Changed"}])  # Raises ReadOnlyError
    # tasks.delete(F("Status") == "Done")  # Raises ReadOnlyError
```

`Allow` grants nothing unless you say so:

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
let a write through. To give a reason, raise `ReadOnlyError` with a message of your own.

```python
from pyquickbase import Quickbase, ReadOnlyError, WriteRequest


def small_batches_only(request: WriteRequest) -> bool:
    if request.row_count is None or request.row_count > 100:
        raise ReadOnlyError("This job only writes up to 100 records at a time.")
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
so use `TableRules` when it can express what you need.

### Combining limits

`all_of()` requires a write to pass every policy, in order, and stops at the first refusal.

```python
from pyquickbase import Allow, Quickbase, TableRules, WriteRequest, all_of


def log_write(request: WriteRequest) -> bool:
    print(request.action, request.table_id, request.row_count)
    return True


rules = TableRules({"bqtasks123": Allow(insert=True)})
qb = Quickbase(realm="acme", token="your_user_token", write_policy=all_of(rules, log_write))
```

Put `TableRules` first if a later policy records what was allowed.

`allowed_writes` limits a client to exact requests, on any table. If you also pass `write_policy`, a
write has to pass both. Passing `read_only=True` with either one is a `ConfigurationError`, as it would
refuse everything they allow.

```python
qb = Quickbase(realm="acme", token="your_user_token", allowed_writes={("POST", "/records")})
```

### What these limits don't do

- **They don't limit reads.** A client with `TableRules` can still read every table the token can reach.
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
| The table, action, or a field isn't allowed | `ReadOnlyError` | Nothing was sent |
| The policy returned anything but `True`, or raised an error | `ReadOnlyError` | Nothing was sent. An error it raised is kept as the `__cause__` |
| A later batch of a write was refused | `WriteError` | **Earlier batches were written**, and are in `error.result` |
| `Allow` with an empty list, or labels instead of IDs | `ConfigurationError` | The rules weren't created |
| `TableRules` with anything but `{table ID: Allow(...)}` | `ConfigurationError` | The rules weren't created |
| An async function as a policy, or `read_only` with a policy | `ConfigurationError` | The client wasn't created |

## Retries

```python
from pyquickbase import Quickbase, RetryPolicy

policy = RetryPolicy(max_retries=2, base_delay=0.5, max_delay=10, max_retry_after=30)
qb = Quickbase(realm="acme", token="your_user_token", retry=policy)
```

`max_retry_after` is the longest `Retry-After` the client will wait for. Past that, it raises
`RateLimitError`. A `Retry-After` of 0 uses the backoff from `base_delay` instead, because Quickbase
can send it while still limiting. Use `max_retries=0` to disable retrying.

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
