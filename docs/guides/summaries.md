---
description: "Counts, totals, and other statistics by group or by period, compared with the period before, and on large tables."
---

# Summarizing records

This page covers working out counts, totals, averages, and other statistics in Quickbase, grouped by field or by
period. To read the records themselves, see [Reading records](reading.md).

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
```

The other examples on this page use `tasks`, made in the same way, with the sync client. With the
async client, use `await` on each call.

[`summarize()`](../reference/summaries.md#Table-summarize) counts records and finds totals, without needing a report built in the UI. Pass the fields to
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

| [`SummaryRow`](../reference/summaries.md#SummaryRow) attribute | What it holds |
|---|---|
| `group` | The values that define the group. Empty without `group_by`. A user is their email, and an empty value is `None` |
| `count` | How many records are in the group, including those with empty values |
| `sum` | The sum of each field, which is `0` if every value is empty |
| `avg`, `median`, `min`, `max` | Each field's result, or `None` if every value is empty. Empty values are left out |
| `std_dev` | Each field's sample standard deviation, or `None` with fewer than two values |
| `distinct_count` | How many different values each field has, leaving out empty ones |
| `change` | With `compare_periods`, how the group changed since the period before |

Groups are sorted by their values, with empty ones last. If nothing matches, you get an empty list, or one
row with a `count` of 0 without `group_by`.

`sum`, `avg`, `median`, and `std_dev` take numeric fields. `min` and `max` also take dates, timestamps,
and times of day. `distinct_count` takes any field you can group by, such as how many clients have open
tasks. You can't group by a multi-select, multi-user, or file field, because a record can hold several
of those values at once. `row.statistic("median")` reads any statistic by name.

A median keeps every value of its field in memory until the summary ends, so set `max_records_scanned`
on a large table. The others keep only running totals.

[Try it in the playground](../playground-app/index.html#add-up-time-across-tables), with simulated data.

## Grouping dates by period

[`Period`](../reference/summaries.md#Period) groups a date or timestamp field by `day`, `week`, `month`, `quarter`, or `year`, labeled `2026-03-14`,
`2026-W11`, `2026-03`, `2026-Q1`, and `2026`, so they sort in order. Which day a timestamp falls on depends on
the time zone, so pass `time_zone` if you don't want UTC:

```python
from zoneinfo import ZoneInfo

from pyquickbase import Period

by_week = Period("Date Created", "week", time_zone=ZoneInfo("America/New_York"))
print(tasks.summarize(group_by=by_week))
```

## Comparing each period with the one before

With `compare_periods=True`, each row's `change` says how its group changed since the period before, so
you don't have to line up the rows and subtract. `group_by` needs exactly one [`Period`](../reference/summaries.md#Period). Any other field in
it stays fixed, so each owner's months are compared with their own.

```python
from pyquickbase import Period

rows = tasks.summarize(
    group_by=[Period("Due Date", "month"), "Status"],
    sum="Amount",
    compare_periods=True,
)
for row in rows:
    if row.change:
        print(row.group["Due Date"], row.change.previous, row.change.count, row.change.values)
```

| [`PeriodChange`](../reference/summaries.md#PeriodChange) attribute | What it holds |
|---|---|
| `previous` | The period it's compared with, such as `2026-02` |
| `count` | How many more records there are, negative for fewer |
| `values` | How much each numeric statistic rose, as `{"sum": {"Amount": 120.0}}`, or `None` where either period has no value |

A period with no records still counts: its count and `sum` are 0, and it has no average or median. The
first period in the result has no `change`, since nothing before it was summarized. A filter that starts or
ends partway through a period makes that period look smaller, so filter on whole periods.

## Large tables

A summary that only counts, with no `group_by` or statistics, asks Quickbase how many records match,
in one request, and reads none, so it takes the same time at any size.

Any other summary reads every matching record, in pages of up to 25,000. A page holds fewer records when the summary
reads more fields, so that it takes about 25 MB of memory however wide it is. Totalling one field of 250,000 records by
month takes about 11 requests and 6 seconds. A report built in the UI does the calculating in Quickbase, and is faster
still. See [Reading summary and chart reports](reports.md#reading-summary-and-chart-reports).

To refuse a scan larger than you expect, set `max_records_scanned`. If more records match, it raises
[`SummaryTooLargeError`](../reference/errors.md#SummaryTooLargeError), a [`InvalidQueryError`](../reference/errors.md#InvalidQueryError), without reading them, and its `matched` and `limit` say by how much. A count isn't
limited by it, as it reads no records. It never returns a total from only part of the data.

```python
from pyquickbase import SummaryTooLargeError

try:
    rows = tasks.summarize(group_by="Status", max_records_scanned=50_000)
except SummaryTooLargeError as error:
    print(error.matched, error.limit)
```

Records can change between pages, so on a table in use, the result may not match any one moment.

## When something is refused

The first two raise before any records are read, though the SDK may read table metadata first to check them.

| What happened | Exception |
|---|---|
| [`summarize()`](../reference/summaries.md#Table-summarize) with a field it can't use, such as text in `sum`, or a multi-select field in `group_by` | [`InvalidQueryError`](../reference/errors.md#InvalidQueryError) |
| `summarize()` finds more records than `max_records_scanned` | [`SummaryTooLargeError`](../reference/errors.md#SummaryTooLargeError), a `InvalidQueryError`, and no records are read |
| `summarize()` finds a value that isn't what its field's type says | [`InvalidResponseError`](../reference/errors.md#InvalidResponseError), and there's no partial result |

See [Handling errors](errors.md) for failures that come from Quickbase, such as a refused token.
