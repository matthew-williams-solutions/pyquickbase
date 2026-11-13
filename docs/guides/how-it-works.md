---
description: "What the SDK does for you: when it contacts Quickbase, how it turns names into IDs, what it caches, and how it pages, batches, and retries."
---

# How pyquickbase works

This page explains what happens between your code and Quickbase, so the rest of the docs make sense.
Each section links to the guide with the details.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")  # Nothing is sent yet
    print(tasks.count())  # Looks up the table, then counts its records
```

## When Quickbase is contacted

A client gives you apps, an app gives you tables, and a table gives you reports. Getting any of these
sends nothing, so a name or ID that doesn't exist only fails on first use, with
[`UnknownTableError`](../reference/errors.md#UnknownTableError) or [`UnknownReportError`](../reference/errors.md#UnknownReportError). Iterating [`query()`](../reference/records.md#Table-query) is lazy too: nothing is fetched until
your loop starts. Other methods contact Quickbase when you call them, apart from a few that only build
something, such as [`app.url()`](../reference/links.md#App-url).

## Names and IDs

Quickbase's API names tables and fields by ID. The SDK lets you use names instead, and sends IDs:

- **A table** is found by its ID or its exact name.
- **A field** is found by its ID, as an `int`, or its exact label, as a `str`. If several fields share
  a label, it raises [`UnknownFieldError`](../reference/errors.md#UnknownFieldError) and lists their IDs, so use one of those.
- **A name it can't find** makes it look once more, in case the table or field is newer than its
  cache, before it raises [`UnknownTableError`](../reference/errors.md#UnknownTableError) or `UnknownFieldError`, which suggest close matches.

Records come back keyed by label, so `record["Status"]` and `record[7]` both work.
[`resolve_field_ids()`](../reference/fields.md#Table-resolve_field_ids) turns labels into IDs if you need them yourself.

## The metadata cache

To turn names into IDs without asking Quickbase every time, the client keeps an app's tables and each
table's fields for five minutes. Changes made through the same client update it at once.

For a change made elsewhere, such as in the Quickbase interface, pass `refresh=True` to `fields()` or
`tables()`, or call [`qb.clear_metadata_cache()`](../reference/client.md#Quickbase-clear_metadata_cache). A name the cache doesn't have yet is looked up again
anyway. To cache for a different time, pass `metadata_cache_seconds` to the client, or 0 to turn the
cache off.

```python
qb.clear_metadata_cache()
print([field.label for field in tasks.fields()])
```

## Values

Values are converted both ways, so you write and read Python types: `date` and timezone-aware
`datetime` objects, [`User`](../reference/records.md#User) for user fields, and [`FileUpload`](../reference/files.md#FileUpload) and [`FileAttachment`](../reference/files.md#FileAttachment) for files. You can
also write a `Decimal` for an exact amount. A write is checked before anything is sent, and a value that can't be
written raises [`InvalidValueError`](../reference/errors.md#InvalidValueError). [Field types](../field-types.md) lists what each type takes and
reads as, including what an empty field reads as.

## Reading in pages

[`query()`](../reference/records.md#Table-query) fetches up to 1,000 records at a time, which `page_size` changes, and fetches the next page
once your loop has used the last one. Each page starts where the last page's records ended, as
Quickbase can [return fewer records than asked](../api-notes.md#confirmed) before the end. Record
ID# is always the last sort key, so records with equal values stay in the same order across pages.

Pages are fetched by position, so deleting records while you read can make others be skipped, with no
error. See [Reading records](reading.md#fetching-records).

## Writing in batches

[`insert()`](../reference/writes.md#Table-insert), [`update()`](../reference/writes.md#Table-update), and [`upsert()`](../reference/writes.md#Table-upsert) send 1,000 rows at a time, which `batch_size` changes. A row
Quickbase refuses doesn't stop the others: the write raises [`WriteError`](../reference/errors.md#WriteError), whose `result` says which
rows were written and why each failed one was refused. See
[Writing records](writing.md#large-writes).

## Retries

A rate limit, or a connection that couldn't be made, is retried for any request, waiting as long as
Quickbase asks or longer each time. A read is also retried after a timeout, a dropped connection, or
a 502, 503, or 504, but a write isn't, as it may already have been applied. [`RetryPolicy`](../reference/client.md#RetryPolicy) sets how
often and how long. See
[Configuring clients](clients.md#retries).

## Sync and async

[`Quickbase`](../reference/client.md#Quickbase) and `AsyncQuickbase` have the same methods with the same arguments. With the async client,
you `await` each call, except a few such as [`app.table()`](../reference/tables.md#App-table) and [`query()`](../reference/records.md#Table-query), which the API reference
marks. The examples in the guides use the sync client.

## Policies

A client given a policy checks each request against it before sending, including requests through
[`qb.request()`](../reference/client.md#Quickbase-request), and raises [`PolicyRefusedError`](../reference/errors.md#PolicyRefusedError) for anything it doesn't allow. See
[Policies](../policies/index.md).
