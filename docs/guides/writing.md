# Writing records

Rows are dictionaries keyed by field label or ID. Python `date`, timezone-aware `datetime`, and
`Decimal` values are converted for you, and `None` clears a field. File fields work differently: see
[File attachments](files.md).

| Method | What it does |
|---|---|
| `table.insert(rows)` | Creates records, and never changes existing ones |
| `table.update(rows)` | Changes records. Each row needs a `Record ID#` |
| `table.upsert(rows, merge_on="Code")` | Updates the record whose unique field matches, or creates one |
| `table.delete(where)` | Deletes matching records and returns the count |

## Creating records

```python
from datetime import date

result = tasks.insert(
    [
        {"Title": "Pour slab", "Status": "Not Started", "Due Date": date(2027, 1, 15)},
        {"Title": "Frame walls", "Status": "Not Started"},
    ]
)
print(result.created_ids)
```

A row that sets `Record ID#` raises `InvalidValueError`, because that would update a record.

## Updating records

Fields you leave out keep their values. A Record ID# that doesn't exist is an error, not a new
record.

```python
result = tasks.update([{"Record ID#": 7, "Status": "Done", "Notes": None}])
print(result.updated_ids, result.unchanged_ids)
```

## Upserting records

`merge_on` must name a field marked as unique, and every row must set it. A row whose value doesn't
match any record **creates a new record**, so use `update()` if that isn't what you want.

```python
result = tasks.upsert([{"Code": "A1", "Title": "Frame walls"}], merge_on="Code")
print(result.created_ids, result.updated_ids)
```

## Deleting records

`where` is required, and it's checked before sending. This matters more here than anywhere else:
Quickbase treats a lowercase operator as "contains", so a typo could delete far more than you meant.

```python
from pyquickbase import F

deleted = tasks.delete(F("Status") == "Cancelled")
print(deleted)
```

## When rows fail

Quickbase writes the rows it can and reports the rest, so a `WriteError` doesn't mean nothing
changed. Its `result` lists both what was written and what failed, and `index` is the row's position
in the list you passed.

```python
from pyquickbase import WriteError

try:
    tasks.insert(rows)
except WriteError as error:
    print(error.result.created_ids)
    for failed in error.result.errors:
        print(rows[failed.index], failed.messages)
```

Large writes are split into batches of 1,000 rows, which you can change with `batch_size`. If a later
batch fails outright, `WriteError` is raised with the results so far. Check `error.result` before
trying again, so you don't write the same rows twice.

A write that times out isn't retried, because Quickbase may already have applied it.
