# CSV and Excel files

This page covers exporting records to CSV and Excel files, and importing a file's rows into a table,
with every value checked before anything is written. Every method here is also on the async client,
where you `await` it.

CSV needs nothing extra. Excel needs `openpyxl`, which `pip install 'pyquickbase[excel]'` installs.

## Exporting records

`to_csv()` and `to_excel()` write what a query matches to a file, with a header row of field labels.
They're on `query()`, a report's `run()`, and any page, and return how many records they wrote.

```python
from pyquickbase import F

open_now = tasks.query(where=F("Status") == "Open", select=["Title", "Due Date", "Amount"])
print(open_now.to_csv("open-tasks.csv"))

tasks.query(select=["Title", "Amount"]).to_excel("tasks.xlsx", sheet="Tasks")
```

Records are written as they're fetched, a page at a time, so a large table never has to fit in memory.
Record ID# is always a column, so a file you export can be imported back to update the same records.

| Value | In a CSV | In Excel |
|---|---|---|
| Date | `2027-01-15` | An Excel date |
| Timestamp | `2027-01-15T09:30:00+00:00` | An Excel date and time, in UTC, as Excel has no time zones |
| Number | As Quickbase returns it | An Excel number |
| Checkbox | `true` or `false` | `TRUE` or `FALSE` |
| User | Their email, or their ID if the client's policy hides emails | The same |
| Multi-select or multi-user | Values separated by `;` | The same |
| File attachment | The latest version's file name | The same |

**Text that a spreadsheet would run as a formula is made safe.** A value starting with `=`, `+`, `-`,
or `@` can run when the file is opened in a spreadsheet, so `to_csv()` writes it with a leading `'`,
which spreadsheets show without it, and `rows_from_file()` removes again. Pass `spreadsheet_safe=False`
for a file only programs will read. In Excel, text is always stored as text, so nothing is added.

`to_csv()` writes UTF-8 with a byte order mark, which Excel needs to show accented letters correctly. Pass
`encoding="utf-8"` for a file without one.

## Importing a file

`rows_from_file()` reads a CSV or Excel file and converts each value for its field. Nothing is written
yet, so you can check what it found, and then pass it to `insert()`, `update()`, `upsert()`, or
`preview_update()`.

```python
from pathlib import Path

Path("new-tasks.csv").write_text(
    'Title,Due Date,Amount\nOrder chairs,2027-03-01,250\nBook venue,2027-03-15,"1,200"\n'
)

rows = tasks.rows_from_file("new-tasks.csv")
if rows.ok:
    result = tasks.insert(rows)
    print(result.created_ids)
else:
    for error in rows.failed_rows:
        print(error)  # Row 3, column 'Due Date': isn't a date in the form YYYY-MM-DD...
```

The first row names each column's field by its exact label. A blank cell leaves a field empty in
`insert()`, and clears it in `update()` and `upsert()`. A row whose cells are all blank is skipped.

| `FileRows` attribute | What it holds |
|---|---|
| `rows` | Each row, as `{field label: value}`, ready to write |
| `row_numbers` | Each row's number in the file, counting the header as row 1, as a spreadsheet does |
| `failed_rows` | Every value that can't be written, each with its `row`, `column`, and `message` |
| `columns` | Which field each column was read into |
| `skipped` | The columns that weren't read |

**A write refuses a `FileRows` with errors, and writes nothing,** listing the first few. Every error in
the file is found at once, so they can all be fixed before trying again. An error never repeats the
value, which may be sensitive.

### How values are read

Each value is converted by its field's type, because Quickbase quietly empties a number field given
text it can't read as a number, such as `1,234.5` or `45%`.

| Field type | Takes |
|---|---|
| Number, currency, rating | `1234.5`, `1,234.5`, or `-0.5` |
| Percent | `45%` or `0.45`, which are the same |
| Duration | Whole milliseconds, as Quickbase stores it |
| Date | `2027-01-15`, or any form `date_format` gives, such as `%m/%d/%Y` |
| Timestamp | ISO 8601, such as `2027-01-15T09:30:00+00:00`, or the form `datetime_format` gives. One without a time zone is read in `tz`, UTC by default |
| Time of day | `13:30`, `13:30:00`, or `1:30 PM` |
| Checkbox | `true`, `false`, `yes`, `no`, `1`, `0`, and a few more, ignoring case |
| User | An email address or a user ID |
| Multi-select or multi-user | Values separated by `;` |
| Text | As written |

Excel's own dates, times, numbers, and true or false values are used as they are.

### Columns that don't match

A column that isn't a field raises `InvalidValueError` before any row is read, suggesting a close
label if there is one. `columns` maps a column to a field, by label or ID, or skips it with `None`:

```python
from pathlib import Path

Path("tasks-export.csv").write_text("Task,Owner Notes,Amount\nPour slab,Call first,5\n")

rows = tasks.rows_from_file("tasks-export.csv", columns={"Task": "Title", "Owner Notes": None})
print(rows.columns, rows.skipped)  # {'Task': 'Title', 'Amount': 'Amount'} ('Owner Notes',)
```

A column for a field that can't be written, such as a formula, a lookup, a file, or Date Created, also
raises an error. Pass `skip_read_only=True` to leave those columns out instead.

### Updating records from an export

Export the fields to change, edit the file, and import it again. Record ID# says which record each row
updates, and `preview_update()` shows what would change first:

```python
tasks.query(select=["Title", "Amount"]).to_csv("amounts.csv")

# ... edit amounts.csv in a spreadsheet ...

rows = tasks.rows_from_file("amounts.csv")
preview = tasks.preview_update(rows)
print(len(preview.changes))
tasks.update(rows)
```

To match on another unique field instead, use `upsert()` with `merge_on`.

### Other formats

- **Tab-separated files** are read when the path ends in `.tsv` or `.tab`. Pass `delimiter` for others.
- **Excel** reads the first worksheet, or the one `sheet` names. A formula's last calculated value is
  read, which a file saved by Excel has, and a file made by another program may not.
- **An open file** works too: text for CSV, and binary for Excel. `format` says which, when it can't
  be told.
- **`.xls`**, Excel's older format, isn't supported. Save it as `.xlsx` or `.csv`.

The whole file is read into memory, on a worker thread with the async client, so a large file doesn't
stop the event loop. For a very large import, split the file, or read it in chunks with
`pandas.read_csv(chunksize=...)` and pass each DataFrame to the write.

### Files you didn't make

Set limits on a file someone else gave you. `max_rows` stops reading once the file has more rows with
values than that, so a file with millions of rows is never held in memory. `max_unzipped_bytes` limits
what an Excel file may unpack to, as it's a zip, and a small file can unpack to something huge. It
defaults to 1 GB. Both raise `FileTooLargeError`, an `InvalidValueError`, with the `limit` it went over
and its `unit`.

```python
from pathlib import Path

from pyquickbase import FileTooLargeError

Path("upload.csv").write_text("Title\nOrder chairs\nBook venue\n")

try:
    rows = tasks.rows_from_file("upload.csv", max_rows=1, max_unzipped_bytes=200_000_000)
except FileTooLargeError as error:
    print(f"Over the limit of {error.limit} {error.unit}")
```

## Writes that would have lost data

Writing a record with a value Quickbase accepts but loses now raises `InvalidValueError`, whether it
comes from a file or not:

- Text that isn't a plain number, such as `"1,234.5"`, for a number field, which Quickbase leaves
  empty. Plain numbers as text, such as `"0.45"`, still work.
- A user as `{"email": ...}`, which Quickbase ignores, leaving the field as it was. Write
  `{"id": ...}`, a `User`, or an email address.
