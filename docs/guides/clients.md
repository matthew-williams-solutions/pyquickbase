# Configuring clients

This page covers choosing a client, limiting what it can read and write, retries, and connection
settings. `Quickbase` and `AsyncQuickbase` take the same options. Use them as context managers so that
connections get closed.

## Sync or async

```python
import asyncio

from pyquickbase import AsyncQuickbase, F, Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    print(qb.app("bq1234567").table("Tasks").count(F("Status") == "Open"))


async def main() -> None:
    async with AsyncQuickbase(realm="acme", token="your_user_token") as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        async for task in tasks.query(where=F("Status") == "Open", limit=5):
            print(task["Title"])


asyncio.run(main())
```

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

A client takes one `policy`, which says what it may read and change. Every request is checked
before it's sent, including through `qb.request()`. A refused one raises `PolicyRefusedError`, and
nothing is sent, though a table method may read the table's metadata first. What the policy hides is
left out of every response, so a hidden table or field acts as if the token couldn't see it.

| Policy | What the client can do | Use it for |
|---|---|---|
| None | Whatever the token can | Your own scripts |
| `Policy.read_only()` | Read whatever the token can, and change nothing | Reports, dashboards, and anything that only looks |
| `Policy(rules=[...])`, bound to an app | Only what its rules allow | An AI agent, or a job with a narrow purpose |

A policy protects against bugs in your code and mistakes by a model. See
[what it doesn't do](#what-a-policy-doesnt-do), and give your token access to as little as possible,
so that Quickbase enforces the limit too.

### Making a client read-only

`Policy.read_only()` guarantees a client can't change data. It only sends GET requests, record
queries, report runs, and formula runs, and reads them as a client without a policy would. Running a
report adds to its usage count in Quickbase, but changes no records. It names no table, so a client
takes it without binding.

```python
from pyquickbase import Policy, Quickbase

with Quickbase(realm="acme", token="your_user_token", policy=Policy.read_only()) as qb:
    print(qb.app("bq1234567").tables())
```

### Writing a policy

A policy is a list of rules over tables and fields, by name and label. Bind it to the app once,
which looks up every name and reports every problem at once, then give the bound policy to the
client.

```python
from pyquickbase import F, Policy, Quickbase, allow, deny, fields, limit, tables

policy = Policy(
    classes={
        "pay": ["Technicians.Hourly Rate"],
        "internal": [("Appointments", "Sync ID"), fields.labeled_like("_*")],
    },
    rules=[
        allow.read(tables.all(), max_records=50),
        allow.insert("Job Tasks", fields.all()),
        allow.update("Job Tasks", ["Status", "Priority", "Due Date", "Notes"]),
        allow.delete("Job Tasks", max_records=3),
        limit.rows("Time Entries", F("Billable") == True),
        deny.all(fields.classed("pay", "internal")),
    ],
)

with Quickbase(realm="acme", token="your_user_token") as setup:
    bound = policy.bind(setup.app("bq1234567"))

with Quickbase(realm="acme", token="your_user_token", policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    tasks.update([{"Record ID#": 1, "Status": "Done"}])  # Allowed
    # tasks.update([{"Record ID#": 1, "Labor Cost": 90}])  # Raises PolicyRefusedError
```

With the async client, it's `await policy.bind(app)`. Bind with a client that has no policy, so it
can see every table and field.

**Anything no rule allows is refused, and a deny always wins.** Each rule names its tables first:
a table's name or ID, a list of them, `tables.all()` for every table in the app and any the client
creates, or `tables.created()` for only those the client creates. Most then name fields: labels or
IDs, `fields.all()`, `fields.new()` for fields added after binding, `fields.classed(...)`, or
`fields.labeled_like(...)`, which matches labels such as `_*`, ignoring case.

| Rule | What it allows |
|---|---|
| `allow.read(tables, fields, ...)` | Reading the fields, and filtering and sorting by them, unless it gives its own `filter=` or `sort=`. It also takes `max_records`, `max_summary_records`, and `user_emails` |
| `allow.filter(...)`, `allow.sort(...)` | Filtering or sorting by fields without reading them |
| `allow.insert(...)`, `allow.update(...)` | Setting the fields in new or existing records. An upsert needs both |
| `allow.delete(tables, max_records=None)` | Deleting records. With `max_records`, the client counts what matches first |
| `allow.download(...)`, `allow.delete_file(...)` | Downloading files, or deleting their versions |
| `allow.report(table, reports)` | Running these reports, by name or ID, or every report with `None` |
| `allow.schema(tables, *verbs, ...)` | Changing the schema, as [below](#schema-changes) |
| `allow.create_tables()` | Creating tables in the bound app |
| `deny.all(selector)`, `deny.read(...)`, `deny.update(...)`, and the rest | Refusing what they name, whatever allows it |
| `limit.rows(tables, where)` | Reaching only the records that match, as [below](#limiting-rows) |

Things to know:

- **Writing a field doesn't need read access to it,** so a client can set a field it can't see.
  Such a field can still be named, but its values aren't returned.
- **`Record ID#` is readable wherever anything is,** because the SDK uses it to page through records.
- **`max_records` limits each query, not the client.** A query needs a `limit`, or a `top`, of at most
  this, and it's refused rather than cut short. Where several rules read a table, the highest
  applies. It defaults to 100, and `max_summary_records` to 10,000.
- **A field you may filter or sort by reveals something about its values,** even if it isn't
  returned, because a model can see which records match and in which order.
- **A report's own filter and sort can use any field,** and someone can change them in the Quickbase
  interface, so list only reports you've checked. A summary report's columns can't be matched to
  fields, so allowing one shows all of them.
- **Some things are refused unless the policy is `Policy.read_only()`:** running a formula, which can
  read any table, an app's details and roles, and any request it doesn't recognize. Use a separate
  client for those.
- **A delete needs `filter` on the fields its filter uses,** because the number deleted shows how
  many records matched.
- **`insert` alone can't be limited to creating records on a table with a custom key field.** If
  someone has changed a table's key field in the Quickbase UI, a row with an existing value in that
  field [changes that record](../api-notes.md#writes). `table.insert()` refuses those tables, so use
  `upsert()`, which needs both `insert` and `update`. `qb.request()` can't look up a table's key
  field, so any write through it without a merge field needs both.

### Classes, and what derived fields read

A class names a group of fields, such as everything about pay, for `fields.classed()`. Each entry is
`"Table.Field"`, `("Table", "Field")` when a name holds a dot, `fields.all()`, or
`fields.labeled_like(...)`, which matches in every table.

**A formula, lookup, or summary is in every class of the fields it reads,** through any chain of
them, including the fields a summary's filter uses. So `deny.all(fields.classed("pay"))` hides a
formula of `Hourly Rate`, a lookup of that formula on another table, and a summary of the lookup. A
formula that can't be read counts as reading every field of its table, and one that calls a function
that reads other records counts as reading every field of the app.

**A new formula, lookup, or summary may only read what the client may read,** as anyone who can read
it sees what it shows. The SDK's methods check this before sending one, and `qb.request()` can't make
one.

### Fields added after binding

The bound policy holds every table and field by ID, as Quickbase never reuses a field's ID within a
table, so a rule can't come to mean another field. A renamed field keeps its rules until you bind
again. A field added after binding gets `new_fields`:

| `new_fields` | A new field is |
|---|---|
| `"read"`, the default | Read, filtered, and sorted wherever a rule reads `fields.all()` or `fields.new()` |
| `"hidden"` | Hidden |
| A class's name | In that class, so the rules for the class apply |

**A new field is never writable** until the policy is bound again, whatever the rules say.

### Limiting rows

`limit.rows(tables, where)` ANDs a condition onto every query, summary, and delete the client sends
to those tables, including through `qb.request()`. The fields it uses may be filtered on.

- **A summary over a row-limited table gets the limit in its filter,** so `ensure_summary_field()`
  and a plan's summaries count only what the client may reach.
- **An existing summary of a row-limited table whose filter doesn't hold the limit is hidden,** as
  is a lookup from a row-limited table, which shows its records on others. A lookup onto a
  row-limited table is fine.
- **Reports, file downloads, and formulas on a row-limited table are refused,** as the limit can't be
  added to them.
- **It doesn't check updates by `Record ID#`,** which name a record without a filter.

It's defense in depth. Quickbase's [record-level rules](../api-notes.md#record-level-rules) for the
token's role are the real boundary, so set those as well.

### Schema changes

`allow.schema(tables, *verbs)` grants schema changes on the table where each change lands:

| Verb | What it allows | Granted on |
|---|---|---|
| `add_field` | Adding fields, of `field_types=[...]` if given | The table |
| `change_field`, `delete_field` | Changing or deleting the fields in `fields=`, or every field | The table |
| `formula` | Making or changing a formula, with `add_field` or `change_field` | The table |
| `lookup` | Adding a lookup, which also needs `read` on the parent's field | The child |
| `summary` | Adding a summary, which also needs `read` on the child's field, and `filter` on every field its filter uses | The parent |
| `link`, `unlink` | Making or deleting a relationship | **Both tables**, as Quickbase adds a link field and an "Add" record formula to the parent. `unlink` also needs `delete_field` wherever its lookups and summaries are |
| `change_table`, `delete_table` | Renaming the table, or deleting it and its records | The table |

A formula can't make a link or rich text (`url` or `rich-text`), as that acts as whoever clicks it.

**Creating a table gives the client a new policy,** with the table in its `created` list, so that
`tables.created()` covers it. `qb.policy` is always the current one, and nothing is changed in place.
Save it with `qb.policy.to_json()` to use in another process, and load it with
`BoundPolicy.from_json()`.

### Asking the policy

A bound policy answers questions without sending anything:

```python
bound.allows("update", "Job Tasks", field="Labor Cost").allowed  # False
print(bound.explain("lookup", "Job Tasks", source=("Technicians", "Hourly Rate")))
bound.offers("Job Tasks").updatable  # What an operation would offer
```

Each returns or explains a `Decision`, with `allowed`, the `layer` that decided, the `rule` as
written, a `reason`, and a `hint` saying what would allow it. `decide()` is the only code that
decides anything, and what operations offer comes from asking it about each field and verb, so a
model is never offered what the policy refuses.

### Rules of your own

For logic the rules can't express, add an object with a `decide(request)` method to `rules`. It sees
each request the built-in rules allow, as a `ReadRequest` or `WriteRequest`, which holds no values,
so it's safe to log. It returns None to leave the request alone, or a `Decision` with
`allowed=False` to refuse it. **It can only refuse,** so a bug in it can't allow more than the rules
do.

```python
from pyquickbase import Decision, Policy, WriteRequest, allow, fields


class SmallBatches:
    def decide(self, request):
        if isinstance(request, WriteRequest) and (request.row_count or 0) > 100:
            return Decision(allowed=False, verb="insert", reason="Up to 100 rows at a time")
        return None


policy = Policy(rules=[allow.insert("Job Tasks", fields.all()), SmallBatches()])
```

`offers()` asks about one field and verb at a time, so a rule that decides each field on its own is
offered exactly what it allows. It runs once per request, so a write split into batches is checked
batch by batch, and it sees each batch's own `row_count`. A custom rule can't be saved with
`to_json()`, so pass it to `from_json(rules=[...])` again.

A `WriteRequest` says what a write would do:

| Attribute | What it holds |
|---|---|
| `action` | `insert`, `update`, `upsert`, `delete`, `delete_file`, a schema change such as `create_field` or `delete_relationship`, or `other` |
| `table_id` | The table's ID, or for a relationship, the child's |
| `field_ids` | The IDs of the fields it sets, or for `update_field` and `delete_field`, the fields it changes |
| `row_count` | How many rows are in this request |
| `app_id` | For `create_table` and `update_table`, the app |
| `parent_table_id` | For a relationship, the parent table |
| `field_type` | For `create_field`, the new field's type |
| `derived` | The formula, lookup, and summary fields it makes or changes |
| `method`, `path` | The HTTP request, for anything `action` doesn't cover |

A `ReadRequest` says what a read would do:

| Attribute | What it holds |
|---|---|
| `action` | `query`, `summarize`, `delete`, `report`, `summary_report`, `file`, `formula`, `tables`, `fields`, `relationships`, `reports`, `app`, `roles`, or `other` |
| `table_id` | The table's ID |
| `field_ids`, `filter_field_ids`, `sort_field_ids` | The fields it returns, filters on, and sorts by |
| `top`, `skip` | How many records it asks for, and how many it passes over |
| `report_id` | The report it runs |
| `method`, `path` | The HTTP request, for anything `action` doesn't cover |

**Treat `None` as "couldn't be determined", not as "nothing".** For example, `field_ids` is `None`
if a row writes a field ID in an unusual form, such as `07`, which Quickbase reads as field 7, and a
query without `sortBy` has `None` for `sort_field_ids`, because the table's default sort would apply.
Have your rule ignore an `action` it doesn't recognize, as a later version can add one.

`upsert` means a write that may do either, which isn't only `table.upsert()`. Any merge on a field
other than `Record ID#` is one, because Quickbase creates a record when the value doesn't match. So is
a write without a merge field that goes through `qb.request()` or to a table with a different key
field, because it can change an existing record.

### What a policy doesn't do

- **It can't see copies of a value.** An address field shows its subfields, and a value pasted into
  another field is just text. Check what each allowed field holds.
- **A write can reveal a value.** An update reports a record as unchanged when a field already held
  the value it was given, so a client that may write a field can test what it holds.
- **It limits this client, not the token.** Code that can create another client, or call the API
  directly, can get around it. To limit that, the token's role has to do it.
- **It doesn't cover what Quickbase does next.** A pipeline or automation can still change other
  tables after an allowed write.
- **It can't limit updates by record.** An update only says which record to change, so checking its
  contents would mean reading it first, and it could change in between. Use a role with
  [record-level rules](../api-notes.md#record-level-rules), which Quickbase enforces itself.

### When something is refused

A refusal's message says what was refused, by name, which rule refused it, and what would allow it:

```
Refused by policy: updating Labor Cost (6) in Job Tasks (bqtasks). No rule allows it. (rule: none)
Nothing was sent. To allow it: add "Labor Cost" to an allow.update("Job Tasks", ...) rule.
```

| What happened | Exception | What's left behind |
|---|---|---|
| A request the policy doesn't allow | `PolicyRefusedError`, whose `decision` says why | Nothing was sent |
| A table or field the policy hides is named | `UnknownTableError` or `UnknownFieldError` | Nothing was sent |
| A later batch of a write was refused | `WriteError` | **Earlier batches were written**, and are in `error.result` |
| A policy that names a table, field, or class that doesn't exist | `ConfigurationError`, listing every one | It wasn't bound |
| A `Policy` that names tables passed to a client without binding | `ConfigurationError` | The client wasn't created |
| A realm, token, or `base_url` that can't be used, such as `http://` to another host | `ConfigurationError` | The client wasn't created |
| `from_env()` with a variable missing or empty | `ConfigurationError` | The client wasn't created |

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
