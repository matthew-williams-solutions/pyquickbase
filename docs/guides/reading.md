# Reading records

The examples use the sync client. With the async client, use `await` on each call and `async for`
on `query()`.

## Tables and fields

`qb.app()` and `app.table()` don't contact Quickbase. A table is found by name or ID when you first
use it, and only within that app, so a table in another app raises `UnknownTableError`.

```python
app = qb.app("bq1234567")
tasks = app.table("Tasks")

print(app.info().name)
print([table.name for table in app.tables()])
print([(field.id, field.label, field.type) for field in tasks.fields()])
print(tasks.relationships())
```

Metadata is cached for five minutes. Set `schema_ttl` on the client to change that, or call
`qb.clear_schema_cache()` after changing a table outside this client. A name or label that isn't in
the cache triggers one refresh before it counts as unknown.

## Writing queries

Build conditions with `F`, naming a field by label or ID. Combine them with `&` and `|`, and wrap
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
| `F("Tags").op("HAS", "x")` | Any other Quickbase operator |

Using `and`, `or`, or a chained comparison like `1 < F("x") < 5` raises `QueryError`, because
Python would otherwise silently drop part of the condition.

A `datetime` needs a timezone, and values containing `{` or `}` are refused. Quickbase can't search
for braces, and they could otherwise change what a query means.

!!! tip "Use `F` for text you don't control"
    `F` escapes values, so user input can't change the query.

### Hand-written query strings

You can also pass a Quickbase query string, using field IDs:

```python
tasks.count("({7.EX.'Done'}OR{7.EX.'Blocked'})AND{9.BF.'today'}")
```

The SDK raises `QueryError` before sending a string with a mistake that Quickbase would silently
accept, such as a lowercase operator or an unknown field. It can't tell an injected condition from
a real one, so don't build these strings from text you don't control. In a value, write an apostrophe
as `\'`.

To add checks of your own, `parse_where()` returns the conditions in a string.

```python
from pyquickbase import parse_where

for condition in parse_where("{7.EX.'Done'}", tasks.fields()):
    print(condition.field_id, condition.operator, condition.value)
```

## Fetching records

`query()` fetches further pages as you iterate, so you can stop at any point.

```python
for task in tasks.query(
    where=F("Status") == "Open",
    select=["Title", "Due Date"],
    order_by=["-Due Date", "Title"],
    limit=50,
):
    print(task["Title"])
```

| Argument | Meaning |
|---|---|
| `where` | A condition built with `F`, or a query string |
| `select` | Field labels or IDs. Defaults to the table's default fields |
| `order_by` | Field labels or IDs. Prefix a label with `-`, or negate an ID, to sort descending |
| `page_size` | Records to request per call to Quickbase. Defaults to 1,000 |
| `limit` | Stop after this many records |

Record ID# is always selected, and it's always the last sort key so that pages stay stable. That
means that without `order_by`, records come back in Record ID# order rather than the table's default
sort.

To control paging yourself, use `query_page()`. To count without fetching, use `count()`.

```python
page = tasks.query_page(where=F("Status") == "Open", top=100, skip=0)
print(len(page.records), page.total, page.has_more, page.next_skip)
```

## Reading values

Read values by label or ID. `dict(record)` gives you `{label: value}`.

```python
task = next(iter(tasks.query(limit=1)))

print(task.id)
print(task["Status"], task[7])
print(dict(task))
```

Date fields come back as `date` objects and timestamp fields as timezone-aware `datetime` objects, or
`None` when empty. Other values are as Quickbase returns them, so a user field is a dictionary and
a numeric field is a `float`.

## Running reports

Find a report on its table by name or ID. Like tables, it's checked when first used. The API can't
create reports, so build them in Quickbase.

```python
print([(report.id, report.name, report.type) for report in tasks.reports()])

for row in tasks.report("Open tasks").run(limit=50):
    print(row["Title"])

page = tasks.report("12").run_page(top=100, skip=0)
print(len(page.records), page.total, page.has_more)
```

The report decides the filter, sort, and columns:

- **Record ID# may be missing.** `record.id` raises `KeyError` if the report doesn't include that
  column, and the default `List All` report doesn't. To check first, use `3 in record`, because
  `hasattr(record, "id")` raises `KeyError` too.
- **Pages may not be stable.** `query()` adds Record ID# as the last sort key to keep pages stable,
  but a report's sort can't be changed. Ties stayed in Record ID# order in a 1,200-record test, but
  that isn't confirmed for larger tables or while records change. If it matters, prefer `query()`.
- **Report IDs are only unique within a table.** Different tables reuse the same IDs, so always get a
  report from its own table.
- **Names can repeat.** A repeated name raises `UnknownReportError`, which lists the IDs to choose
  from. An ID takes priority over a name.

Only table reports can be read as records. Summary, chart, and ask-the-user reports raise
`UnsupportedReportError` before anything runs. Summary and chart results number their columns by
position, so a total would otherwise read as a Record ID#. To get their raw results, use
`qb.request("POST", f"/reports/{report_id}/run", params={"tableId": table_id})`. Quickbase can't run
ask-the-user reports through the API.

Running a report adds to its usage count in Quickbase, even if the run fails, but changes no records.
Report details come from the same cache as other metadata, so `usedCount` in `raw` can be out of
date.
