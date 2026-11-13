---
description: "Finding and running reports built in the Quickbase interface, including summary and chart reports."
---

# Running reports

This page covers running the reports built in the Quickbase interface. To read records without a report, see
[Reading records](reading.md).

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
```

The other examples on this page use `tasks`, made in the same way, with the sync client. With the
async client, use `await` on each call, and `async for` on `run()`.

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
| `table` | `run()` or [`run_page()`](../reference/reports.md#Report-run_page) | Records, as from [`query()`](../reference/records.md#Table-query) |
| `summary` or `chart` | [`run_summary()`](../reference/reports.md#Report-run_summary) | Keyed by column label |

Calling the other method raises [`UnsupportedReportError`](../reference/errors.md#UnsupportedReportError), as does every method on a report of another
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
- **Names can repeat.** A repeated name raises [`UnknownReportError`](../reference/errors.md#UnknownReportError), which lists the IDs to choose
  from. An ID takes priority over a name.

Only table reports can be read as records. Summary, chart, and ask-the-user reports raise
`UnsupportedReportError` before anything runs, because a summary's column IDs are positions rather than
fields, so a total could read as a Record ID#.

Running a report adds to its usage count in Quickbase, but changes no records.
Report details come from the same cache as other metadata, so `usedCount` in `raw` can be out of
date, and so can the details used to check that a report is a table report. Quickbase's UI doesn't let
you change a saved report's type.

[Try it in the playground](../playground-app/index.html#run-reports), with simulated data.

## Reading summary and chart reports

[`run_summary()`](../reference/reports.md#Report-run_summary) reads a report built in the UI as `{column label: value}` rows, with Quickbase doing the
calculating. A summary report labels its columns with the kind of total, such as `Hours (tot)`.

```python
rows = tasks.report("Hours by Project").run_summary()
for row in rows:
    print(row["Project"], row["Hours (tot)"])
```

Values are what Quickbase returns, so unlike `run()`, dates aren't converted, and a chart's dates are text
in the app's format, such as `01-01-2026`. A report that asks the user for a value can't run through the API
at all, and a report with two columns of the same label raises [`UnsupportedReportError`](../reference/errors.md#UnsupportedReportError), as a row could only
hold one of them. Each run adds to the report's usage count.

## When something is refused

These raise before the report is run, though the SDK may read table metadata first to check them.

| What happened | Exception |
|---|---|
| The table has no such report, or several with that name | [`UnknownReportError`](../reference/errors.md#UnknownReportError) |
| `run()` on a summary, chart, or ask-the-user report, or [`run_summary()`](../reference/reports.md#Report-run_summary) on a table or ask-the-user report, or one with two columns of the same label | [`UnsupportedReportError`](../reference/errors.md#UnsupportedReportError) |

See [Handling errors](errors.md) for failures that come from Quickbase, such as a refused token.
