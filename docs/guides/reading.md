# Reading records

This page covers finding tables and fields, querying records, reading their values, and running
reports. To write records, see [Writing records](writing.md).

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    print(tasks.count())
```

The other examples on this page use `qb` and `tasks`, made in the same way, with the sync client. With
the async client, use `await` on each call and `async for` on `query()`.

!!! tip "Try it without installing anything"
    The [playground](../playground.md) runs these in your browser, against simulated sample data.

## Tables and fields

`qb.app()` and `app.table()` don't contact Quickbase. A table is found by name or ID when you first
use it, and only within that app, so a table in another app raises `UnknownTableError`.

```python
app = qb.app("bq1234567")
tasks = app.table("Tasks")

print(app.info().name)
print([table.name for table in app.tables()])
print([(field.id, field.label, field.type) for field in tasks.fields()])
print(tasks.relationships())  # Only those where this table is the child
```

Metadata is cached for five minutes. Set `schema_ttl` on the client to change that, or call
`qb.clear_schema_cache()` after changing a table outside this client. A name or label that isn't in
the cache triggers one refresh before it counts as unknown.

[Try it in the playground](../playground-app/index.html#explore-the-app), with simulated data.

## Getting one record

`first()` returns the first match, and `get()` finds a record by its Record ID#. Each returns None if
there's no such record, rather than raising an error.

```python
task = tasks.first(where=F("Status") == "Open", order_by="Due Date", select=["Title"])
same_task = tasks.get(7, select=["Title"])
```

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
| `F("Tags").has("x")`, `.not_has("x")` | One whole value of a multi-select or list-user field |
| `F("Due Date").op("IR", "last 7 days")` | Any other Quickbase operator |
| `F("Owner") == "a@example.com"` | A `user` or `multiuser` field, by email or user ID |

Using `and`, `or`, or a chained comparison like `1 < F("x") < 5` raises `QueryError`, because
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

### Filters as plain data

An AI model or a web form can't send `F`, only JSON. `where_from_dict()` turns that into the same kind
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

`query()` fetches further pages as you iterate, so you can stop at any point.

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
| `where` | A condition built with `F`, or a query string |
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

To count without fetching, use `count()`.

Field objects such as `F("Status")` name exactly that field, so a leading `-` in one is part of the
label.

[Try it in the playground](../playground-app/index.html#find-open-tasks), with simulated data.

## Fetching one page at a time

`query_page()` fetches a single page, for when you control the paging yourself, such as behind a list
endpoint. It takes the same arguments as `query()`, with `limit` and `skip` in place of `page_size` and
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

## Summarizing records

`summarize()` counts records and finds totals, without needing a report built in the UI. Pass the fields to
group by, and what to calculate. Every result is keyed by field label.

```python
from pyquickbase import F, Period

rows = tasks.summarize(
    group_by=["Status", Period("Due Date", "month")],
    where=F("Project") == "Roof",
    sum="Amount",
    avg="Amount",
    max="Due Date",
)
for row in rows:
    print(row.group["Status"], row.group["Due Date"], row.count, row.sum["Amount"])
```

Leave out `group_by` for one row covering every matching record:

```python
overall = tasks.summarize(sum="Amount", where=F("Status") == "Open")[0]
print(overall.count, overall.sum["Amount"])
```

| `SummaryRow` attribute | What it holds |
|---|---|
| `group` | The values that define the group. Empty without `group_by`. A user is their email, and an empty value is `None` |
| `count` | How many records are in the group, including those with empty values |
| `sum` | The sum of each field, which is `0` if every value is empty |
| `avg`, `min`, `max` | Each field's result, or `None` if every value is empty. Empty values don't count toward an average |

Groups are sorted by their values, with empty ones last. If nothing matches, you get an empty list, or one
row with a `count` of 0 without `group_by`.

`sum` and `avg` take numeric fields. `min` and `max` also take dates, timestamps, and times of
day. You can't group by a multi-select, multi-user, or file field, because a record can hold several of
those values at once.

[Try it in the playground](../playground-app/index.html#add-up-time-across-tables), with simulated data.

### Grouping dates by period

`Period` groups a date or timestamp field by `day`, `week`, `month`, `quarter`, or `year`, labeled `2026-03-14`,
`2026-W11`, `2026-03`, `2026-Q1`, and `2026`, so they sort in order. Which day a timestamp falls on depends on
the time zone, so pass `tz` if you don't want UTC:

```python
from zoneinfo import ZoneInfo

from pyquickbase import Period

by_week = Period("Date Created", "week", tz=ZoneInfo("America/New_York"))
print(tasks.summarize(group_by=by_week))
```

### Large tables

A summary that only counts, with no `group_by`, `sum`, `avg`, `min`, or `max`, asks Quickbase how many records match,
in one request, and reads none, so it takes the same time at any size.

Any other summary reads every matching record, in pages of up to 25,000. A page holds fewer records when the summary
reads more fields, so that it takes about 25 MB of memory however wide it is. Totalling one field of 250,000 records by
month takes about 11 requests and 6 seconds. A report built in the UI does the calculating in Quickbase, and is faster
still. See [Reading summary and chart reports](#reading-summary-and-chart-reports).

To refuse a scan larger than you expect, set `max_records_scanned`. If more records match, it raises
`TooManyRecordsError`, a `QueryError`, without reading them, and its `matched` and `limit` say by how much. A count isn't
limited by it, as it reads no records. It never returns a total from only part of the data.

```python
from pyquickbase import TooManyRecordsError

try:
    rows = tasks.summarize(group_by="Status", max_records_scanned=50_000)
except TooManyRecordsError as error:
    print(error.matched, error.limit)
```

Records can change between pages, so on a table in use, the result may not match any one moment.

## Running reports

Find a report on its table by name or ID. Like tables, it's checked when first used. The API can't
create reports, so build them in Quickbase.

```python
print([(report.id, report.name, report.type) for report in tasks.reports()])

for row in tasks.report("Open tasks").run(max_total=50):
    print(row["Title"])

page = tasks.report("12").run_page(limit=100, skip=0)
print(len(page.records), page.total, page.has_more)
```

Which method reads a report depends on its type, which `report.info().type` gives:

| `report.info().type` | Read it with | Rows are |
|---|---|---|
| `table` | `run()` or `run_page()` | Records, as from `query()` |
| `summary` or `chart` | `summary_rows()` | Keyed by column label |

Calling the other method raises `UnsupportedReportError`, as does every method on a report of another
type, or one whose `incomplete` is true, such as one that asks the user for a value. The report isn't
run.

The report decides the filter, sort, and columns:

- **Record ID# may be missing.** `record.id` raises `KeyError` if the report doesn't include that
  column, and the default `List All` report doesn't. To check first, use `3 in record`, because
  `hasattr(record, "id")` raises `KeyError` too.
- **Pages may not be stable.** `query()` adds Record ID# as the last sort key to keep pages stable,
  but a report's sort can't be changed. Ties stayed in Record ID# order in a 1,200-record test, but
  that isn't confirmed for larger tables or while records change. If it matters, prefer `query()`.
- **Columns can be missing for restricted users.** A column the user's role can't see is left out with
  no error, so `record["Salary"]` raises `KeyError` for them.
- **Report IDs are only unique within a table.** Different tables reuse the same IDs, so always get a
  report from its own table.
- **Names can repeat.** A repeated name raises `UnknownReportError`, which lists the IDs to choose
  from. An ID takes priority over a name.

Only table reports can be read as records. Summary, chart, and ask-the-user reports raise
`UnsupportedReportError` before anything runs, because a summary's column IDs are positions rather than
fields, so a total could read as a Record ID#.

Running a report adds to its usage count in Quickbase, but changes no records.
Report details come from the same cache as other metadata, so `usedCount` in `raw` can be out of
date, and so can the details used to check that a report is a table report. Quickbase's UI doesn't let
you change a saved report's type.

[Try it in the playground](../playground-app/index.html#run-reports), with simulated data.

### Reading summary and chart reports

`summary_rows()` reads a report built in the UI as `{column label: value}` rows, with Quickbase doing the
calculating. A summary report labels its columns with the kind of total, such as `Hours (tot)`.

```python
rows = tasks.report("Hours by Project").summary_rows()
for row in rows:
    print(row["Project"], row["Hours (tot)"])
```

Values are what Quickbase returns, so unlike `run()`, dates aren't converted, and a chart's dates are text
in the app's format, such as `01-01-2026`. A report that asks the user for a value can't run through the API
at all, and a report with two columns of the same label raises `UnsupportedReportError`, as a row could only
hold one of them. Each run adds to the report's usage count.

## When something is refused

These all raise before the query is sent, though the SDK may read table metadata first to check them.

| What happened | Exception |
|---|---|
| The app has no such table, or the table is in another app | `UnknownTableError` |
| `select`, `order_by`, or `where` names a field the table doesn't have, or the token can't see | `UnknownFieldError` |
| `where` can't be sent as written, such as a lowercase operator in a query string, or `_curuser_` on a user field through `F` | `QueryError` |
| `where_from_dict()` got data it can't use, such as an unknown operator, a missing `value`, or `today` on a date field | `QueryError` |
| `limit`, `page_size`, or `max_total` below 1, or a negative `skip` | `QueryError` |
| `select` or `order_by` isn't a field or a list of them | `QueryError` |
| `get()` with something other than a positive integer | `InvalidValueError` |
| Reading a field the record doesn't hold | `KeyError` |
| The table has no such report, or several with that name | `UnknownReportError` |
| `run()` on a summary, chart, or ask-the-user report, or `summary_rows()` on a table or ask-the-user report, or one with two columns of the same label | `UnsupportedReportError` |
| `summarize()` with a field it can't use, such as text in `sum`, or a multi-select field in `group_by` | `QueryError` |
| `summarize()` finds more records than `max_records_scanned` | `TooManyRecordsError`, a `QueryError`, and no records are read |
| `summarize()` finds a value that isn't what its field's type says | `InvalidResponseError`, and there's no partial result |

See [Handling errors](errors.md) for failures that come from Quickbase, such as a refused token.
