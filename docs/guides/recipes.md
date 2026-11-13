---
description: "Complete examples for common jobs: exporting, syncing a spreadsheet, finding what changed, safe bulk updates and deletes, and writes you can send again."
---

# Recipes

Complete examples for common jobs, each built from methods the other guides explain. Each one uses
`tasks`, a table made like this, with the sync client:

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
```

For policies to go with these, see the [policy recipes](../policies/recipes.md).

## Export records to a CSV file each night

`to_csv()` writes records as they're fetched, so a large export doesn't fill memory. Run this on a
schedule, and each day gets its own file.

```python
from datetime import date

from pyquickbase import F

still_open = tasks.query(where=F("Status") == "Open", select=["Title", "Due Date", "Amount"])
count = still_open.to_csv(f"open-tasks-{date.today()}.csv")
print(count, "tasks exported")
```

See [CSV and Excel files](spreadsheets.md).

## Find what changed since the last run

Every table has a `Date Modified` field. Keep the time each run starts, and ask for records changed
since the one before. A `datetime` in a query needs a timezone.

```python
from datetime import datetime, timedelta, timezone

from pyquickbase import F

last_run = datetime.now(timezone.utc) - timedelta(days=1)
changed = tasks.query(where=F("Date Modified") > last_run, select=["Title", "Date Modified"])
for task in changed:
    print(task["Title"], task["Date Modified"])
```

## Sync a spreadsheet without making duplicates

[`upsert()`](../reference/writes.md#Table-upsert) updates the record whose unique field matches each row, and creates one where none does,
so running the same file twice changes nothing the second time. Checking the whole file first means
a bad value stops the sync before anything is written.

```python
from pathlib import Path

Path("tasks.csv").write_text("Code,Title,Amount\nT-1,Order chairs,250\nT-2,Book venue,1200\n")

rows = tasks.rows_from_file("tasks.csv")
rows.raise_for_failures()  # Raises InvalidValueError, listing bad values, and writes nothing
result = tasks.upsert(rows, merge_on="Code")
print(len(result.created_ids), "created,", len(result.updated_ids), "updated")
```

`Code` must be marked unique in Quickbase. See
[CSV and Excel files](spreadsheets.md#importing-a-file) and
[Writing records](writing.md#upserting-records).

## Make a write safe to send again

A write that times out may still have been saved, and isn't retried for you. Writing by a unique
field instead of creating a record means sending it again can't make a second one: Quickbase
[reports a repeated write as unchanged](../api-notes.md#payments-and-repeated-events).

```python
from pyquickbase import RequestTimeoutError

row = {"Code": "INV-1001", "Title": "Invoice 1001", "Amount": 250}
try:
    tasks.upsert([row], merge_on="Code")
except RequestTimeoutError:
    tasks.upsert([row], merge_on="Code")  # Safe, whether or not the first one was saved
```

See [Handling errors](errors.md#when-a-write-may-have-landed).

## Update many records, after checking what would change

Build the rows from a query, then preview them. A preview changes nothing, so you can show it to
someone, or stop if it's more than you expected.

```python
from pyquickbase import F

rows = [
    {"Record ID#": task.id, "Notes": "Checked"}
    for task in tasks.query(where=F("Project") == "Roof", select=["Notes"])
]
preview = tasks.preview_update(rows)
print(len(preview.changes), "would change,", len(preview.unchanged_ids), "already hold this")
if len(preview.changes) <= 100:
    result = tasks.update(rows)
    print(len(result.updated_ids), "updated")
```

See [Writing records](writing.md#previewing-a-write).

## Delete records, with a limit on how many

A preview counts what a delete would match, so a filter that's wider than you meant can be caught
before anything is deleted.

```python
from pyquickbase import F

cancelled = F("Status") == "Cancelled"
page = tasks.preview_delete(cancelled, select=["Title"], limit=10)
print(page.total, "would be deleted, such as", [task["Title"] for task in page.records])
if page.total <= 100:
    print(tasks.delete(cancelled), "deleted")
```

A client can also be held to a limit, with `allow.delete(..., max_records_per_delete=100)` in its
[policy](../policies/rules.md).
