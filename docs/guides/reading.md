---
description: "Tables, fields, queries with F, paging, record values, DataFrames, and what each refusal raises."
---

# Reading records

This page covers finding tables and fields, querying records, and reading their values. To total or
count them, see [Summaries](summaries.md), and to run a report built in Quickbase, see
[Reports](reports.md). To write records, see [Writing records](writing.md).

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    print(tasks.count())
```

The other examples on this page use `qb` and `tasks`, made in the same way, with the sync client. With
the async client, use `await` on each call and `async for` on [`query()`](../reference/records.md#Table-query).

!!! tip "Try it without installing anything"
    The [playground](../playground.md) runs these in your browser, against simulated sample data.

## Tables and fields

[`qb.app()`](../reference/client.md#Quickbase-app) and [`app.table()`](../reference/tables.md#App-table) don't contact Quickbase. A table is found by name or ID when you first
use it, and only within that app, so a table in another app raises [`UnknownTableError`](../reference/errors.md#UnknownTableError).

```python
app = qb.app("bq1234567")
tasks = app.table("Tasks")

print(app.info().name)
print([table.name for table in app.tables()])
print([(field.id, field.label, field.type) for field in tasks.fields()])
print(tasks.relationships())  # Only those where this table is the child
```

Metadata is cached for five minutes. Set `metadata_cache_seconds` on the client to change that, or call
[`qb.clear_metadata_cache()`](../reference/client.md#Quickbase-clear_metadata_cache) after changing a table outside this client. A name or label that isn't in
the cache triggers one refresh before it counts as unknown.

[Try it in the playground](../playground-app/index.html#explore-the-app), with simulated data.

## Getting one record

[`first()`](../reference/records.md#Table-first) returns the first match, and [`get()`](../reference/records.md#Table-get) finds a record by its Record ID#. Each returns None if
there's no such record, rather than raising an error.

```python
task = tasks.first(where=F("Status") == "Open", order_by="Due Date", select=["Title"])
same_task = tasks.get(7, select=["Title"])
```

## Writing queries

Build conditions with [`F`](../reference/queries.md#F), naming a field by label or ID. Combine them with `&` and `|`, and wrap
each one in parentheses, because Python gives those operators high precedence.

```python
from datetime import date
from pyquickbase import F

where = ((F("Status") == "Open") | (F("Status") == "Blocked")) & (F("Due Date") < date.today())
print(tasks.count(where))
```

| Python | Matches |
|---|---|
| `F("Status") == "Done"`, `!=` | Equal or not equal, ignoring case |
| `F("Hours") > 10`, `>=`, `<`, `<=` | Numbers and dates |
| `F("Notes") == None` | Empty fields |
| `F("Active") == True` | Checkboxes |
| `F("Title").contains("x")`, `.not_contains("x")` | Text anywhere in the value |
| `F("Title").starts_with("x")`, `.not_starts_with("x")` | Text at the start of the value |
| `F("Status").is_in(["Done", "Blocked"])` | Any of the values |
| `F("Tags").has("x")`, `.not_has("x")` | One whole value of a multi-select or list-user field |
| `F("Due Date").op("IR", "last 7 days")` | Any other Quickbase operator |
| `F("Owner") == "a@example.com"` | A `user` or `multiuser` field, by email or user ID |

Using `and`, `or`, or a chained comparison like `1 < F("x") < 5` raises [`InvalidQueryError`](../reference/errors.md#InvalidQueryError), because
Python would otherwise silently drop part of the condition.

A `datetime` needs a timezone. On a date field, Quickbase reads text such as `today` or `1 day ago` as a
date, and text it can't read matches nothing with no error, so prefer `date` objects. Some text would otherwise change what a query means, because Quickbase
reads it as query syntax even inside quotes, so `F` handles it for you:

- Values containing `{` or `}` are refused, because Quickbase can't search for them.
- Values like `_FID_6` are refused, because Quickbase compares them with that field, so
  `F("Code") == "_FID_7"` could match every record. These can't be searched for either.
- `_curuser_` is refused on a `user` or `multiuser` field, because Quickbase reads it as the token's
  owner, so `F("Team") == "_curuser_"` could match that person's records. To mean that on purpose, write a
  [query string](#hand-written-query-strings).
- An uppercase `OR` or `AND` standing alone is sent in lowercase, because `a OR b` would otherwise match
  `a` and `b` rather than that text. Comparisons ignore case, so this still finds it.

!!! tip "Use `F` for text you don't control"
    `F` escapes values, so user input can't change the query. A query string gets none of this
    protection: there, `{6.EX.'a OR b'}` means `a` or `b`.

[Try it in the playground](../playground-app/index.html#combine-conditions), with simulated data.

### Hand-written query strings

You can also pass a Quickbase query string, using field IDs:

```python
tasks.count("({7.EX.'Done'}OR{7.EX.'Blocked'})AND{9.BF.'today'}")
```

The SDK raises `InvalidQueryError` before sending a string with a mistake that Quickbase would silently
accept, such as a lowercase operator or an unknown field. It can't tell an injected condition from
a real one, so don't build these strings from text you don't control. In a value, write an apostrophe
as `\'`.

To add checks of your own, [`parse_where()`](../reference/queries.md#parse_where) returns the conditions in a string.

```python
from pyquickbase import parse_where

for condition in parse_where("{7.EX.'Done'}", tasks.fields()):
    print(condition.field_id, condition.operator, condition.value)
```

### Filters as plain data

An AI model or a web form can't send `F`, only JSON. [`where_from_dict()`](../reference/queries.md#where_from_dict) turns that into the same kind
of condition, and works anywhere `F` does.

```python
from pyquickbase import where_from_dict

where = where_from_dict(
    {
        "all": [
            {"field": "Status", "op": "==", "value": "Open"},
            {
                "any": [
                    {"field": "Due Date", "op": "<", "value": "2027-01-01"},
                    {"field": "Amount", "op": ">", "value": 100},
                ]
            },
        ]
    }
)
print(tasks.count(where))
```

It's stricter than `F`, because plain data can't hold a `date` object. For a model's filters, see
[Filters from a model](../agents/untrusted-input.md#filters-from-a-model).

## Fetching records

[`query()`](../reference/records.md#Table-query) fetches further pages as you iterate, so you can stop at any point.

```python
for task in tasks.query(
    where=F("Status") == "Open",
    select=["Title", "Due Date"],
    order_by=["-Due Date", "Title"],
    max_total=50,
):
    print(task["Title"])
```

| Argument | Meaning |
|---|---|
| `where` | A condition built with [`F`](../reference/queries.md#F), or a query string |
| `select` | Field labels, IDs, or field objects. Defaults to the table's default fields |
| `order_by` | Field labels, IDs, or field objects. Prefix a label with `-`, or negate an ID, to sort descending |
| `page_size` | Records to request per call to Quickbase. Defaults to 1,000 |
| `max_total` | Stop after this many records in all, across pages |

Record ID# is always selected, and it's always the last sort key so that pages stay stable. That
means that without `order_by`, records come back in Record ID# order rather than the table's default
sort.

Nothing is fetched until your loop starts. Then `query()` fetches up to `page_size` records at a time,
and fetches the next page only once your loop has used the last record of the current one. It stops
when nothing more matches or `max_total` is reached, and the last request asks only for what's left.
Breaking out of the loop fetches no more pages.

Paging uses offsets, so if records are deleted while you read, others can be skipped without any error.
Avoid deleting from the set you're reading, or read it all first.

To count without fetching, use [`count()`](../reference/records.md#Table-count).

Field objects such as `F("Status")` name exactly that field, so a leading `-` in one is part of the
label.

[Try it in the playground](../playground-app/index.html#find-open-tasks), with simulated data.

## Fetching one page at a time

[`query_page()`](../reference/records.md#Table-query_page) fetches a single page, for when you control the paging yourself, such as behind a list
endpoint. It takes the same arguments as [`query()`](../reference/records.md#Table-query), with `limit` and `skip` in place of `page_size` and
`max_total`.

```python
from pyquickbase import F

skip = 0
while True:
    page = tasks.query_page(where=F("Status") == "Open", select=["Title"], limit=100, skip=skip)
    for task in page.records:
        print(task["Title"])
    if not page.has_more:
        break
    skip = page.next_skip
```

| Attribute | Meaning |
|---|---|
| `records` | This page's records |
| `total` | Every match, not just this page |
| `skip` | The `skip` this page was fetched with |
| `has_more` | Whether any matches come after this page |
| `next_skip` | The `skip` that fetches the following page |

Skipping past the end returns no records rather than raising an error, with `total` still correct.

[Try it in the playground](../playground-app/index.html#count-and-page), with simulated data.

## DataFrames

`to_pandas()` and `to_polars()` fetch what a query matches into a DataFrame, with a column for each
field it returns. They're on [`query()`](../reference/records.md#Table-query), a report's `run()`, and any page. Install the library you use
with the SDK's extra, such as `pip install 'pyquickbase[pandas]'` or `pip install 'pyquickbase[polars]'`.

```python
from pyquickbase import F

frame = tasks.query(where=F("Status") == "Open", select=["Title", "Due Date"]).to_pandas()
print(frame.columns.tolist())  # ['Record ID#', 'Title', 'Due Date']
```

- Dates, timestamps, numbers, and lists keep their types, so a timestamp column is timezone-aware.
- A user field holds the user's email, or their ID if the client's policy hides emails, and a file field
  holds the latest version's file name. Use `query()` for the [`User`](../reference/records.md#User) and [`FileAttachment`](../reference/files.md#FileAttachment) objects.
- A query with no matches still gives a DataFrame with its columns.
- It reads every page first, so set `max_total` on a large table.

To write a DataFrame back, pass it to [`insert()`](../reference/writes.md#Table-insert), [`update()`](../reference/writes.md#Table-update), or [`upsert()`](../reference/writes.md#Table-upsert), as
[Writing records](writing.md#writing-a-dataframe) shows.

## Reading values

Read values by label or ID. `dict(record)` gives you `{label: value}`.

```python
task = next(iter(tasks.query(max_total=1)))

print(task.id)
print(task["Status"], task[7])
print(dict(task))
```

Date fields come back as `date` objects and timestamp fields as timezone-aware `datetime` objects, or
`None` when empty. Other values are as Quickbase returns them, so a user field is a dictionary and
a numeric field is a `float`. [Field types](../field-types.md) lists what every type reads as, including
when it's empty.

**A record only holds the fields you selected.** Without `select`, that's the table's default fields, so
a built-in field such as `Record Owner` may be missing. Reading a missing field raises `KeyError`, and
`3 in record` checks for one first.

[Try it in the playground](../playground-app/index.html#read-values), with simulated data.

## When something is refused

These all raise before the query is sent, though the SDK may read table metadata first to check them.

| What happened | Exception |
|---|---|
| The app has no such table, or the table is in another app | [`UnknownTableError`](../reference/errors.md#UnknownTableError) |
| `select`, `order_by`, or `where` names a field the table doesn't have, or the token can't see | [`UnknownFieldError`](../reference/errors.md#UnknownFieldError) |
| `where` can't be sent as written, such as a lowercase operator in a query string, or `_curuser_` on a user field through [`F`](../reference/queries.md#F) | [`InvalidQueryError`](../reference/errors.md#InvalidQueryError) |
| [`where_from_dict()`](../reference/queries.md#where_from_dict) got data it can't use, such as an unknown operator, a missing `value`, or `today` on a date field | `InvalidQueryError` |
| `limit`, `page_size`, or `max_total` below 1, or a negative `skip` | `InvalidQueryError` |
| `select` or `order_by` isn't a field or a list of them | `InvalidQueryError` |
| [`get()`](../reference/records.md#Table-get) with something other than a positive integer | [`InvalidValueError`](../reference/errors.md#InvalidValueError) |
| Reading a field the record doesn't hold | `KeyError` |

See [Handling errors](errors.md) for failures that come from Quickbase, such as a refused token.
