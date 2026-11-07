# Giving a model operations

## Giving a model operations

`table.operation()` turns one table operation into a tool for a model: a JSON Schema of the arguments it
may send, and `run()`, which checks them against the same lists before calling the table. They're built
together, so the model is offered exactly what `run()` accepts. Give each tool its own operation, and the
model can search, count, change, and read records without you writing each call by hand.

```python
from pyquickbase import AsyncQuickbase, F, QuickbaseError, tool_error, tool_result


async def make_search(qb: AsyncQuickbase):
    tasks = qb.app("bq1234567").table("Tasks")
    return await tasks.operation(
        "query",
        fields=["Title", "Status", "Technician", "Due Date"],
        descriptions={"Technician": "ID of the assigned technician."},
        max_records=50,
    )


# At startup: search = await make_search(qb)
# The tool's definition: {"name": "search_tasks", "input_schema": search.schema}


async def search_tasks(search, project: str, arguments: dict) -> dict:
    """The tool. The model sends `arguments`, and your code fixes the project."""
    try:
        page = await search.run(arguments, scope=F("Project") == project)
        return tool_result(page, compact=True)
    except QuickbaseError as error:
        return tool_error(error)
```

The schema goes in your framework's tool definition, under the name it uses, such as `input_schema` or
`parameters`. It's plain JSON Schema with no `$ref`, and a new dictionary each time, so you can add to it.

### What each operation takes

| Operation | The model sends | Runs | Returns |
|---|---|---|---|
| `query` | `where`, `select`, `order_by`, `limit`, `skip` | `query_page()` | `Page` |
| `count` | `where` | `count()` | An `int`, which `tool_result()` shows as `count` |
| `summarize` | `where`, `group_by`, `total`, `average`, `minimum`, `maximum` | `summarize()` | A list of `SummaryRow` |
| `insert` | `records` | `insert()` | `WriteResult` |
| `update` | `records`, each with `Record ID#` | `update()` | `WriteResult` |
| `delete` | `where`, which is required. It's refused if it matches more than `max_records` | `delete()` | An `int`, which `tool_result()` shows as `deleted_count` |
| `preview_update` | As `update` | `preview_update()` | `UpdatePreview` |
| `preview_delete` | `where`, `select`, `limit`. Give it the same `max_records` as `delete`, and `tool_result()` lists a blocker when a delete would be refused | `preview_delete()` | `Page` |
| `run_report` | `report`, as its ID, its ID as a number, or its name, and `limit`, `skip` | `Report.run_page()` | `Page` |
| `run_summary_report` | `report`, as for `run_report`, and `limit` | `Report.summary_rows()` | A list of rows |
| `run_formula` | `record_id` | `run_formula()`, with the formula your code gives | `FormulaResult` |
| `download_file` | `record_id`, `field`, `version` | `download_file()` | `FileDownload` |
| `delete_file_version` | `record_id`, `field`, `version` | `delete_file_version()` | None |

`where` is the format of [`where_from_dict()`](untrusted-input.md#filters-from-a-model). `order_by` is a list of
`{"field": ..., "direction": "asc" or "desc"}`, and `group_by` takes labels, or
`{"field": ..., "unit": "month"}` to group a date. Whatever `run()` returns, `tool_result()` takes.

The schema lists one spelling of each value, but `run()` also reads the ones models tend to write,
each as the one value it means: `starts with` or `startsWith` for `starts_with`, `eq` or `<>` for
`==` or `!=`, `and` and `or` for `all` and `any`, `sort` for `order_by`, and `"order": "DESC"` or
`"descending": true` for `"direction": "desc"`. Labels are never guessed, so a label in the wrong
case is still refused, with a hint that names the right one.

### What a schema offers

The schema lists only what the client may do, so a model isn't offered a field that would be refused:

| Part | Offers | Limited by |
|---|---|---|
| `where` | Fields it may filter on | `allow.read()` or `allow.filter()` |
| `select`, `group_by`, totals | Fields it may return | `allow.read()` |
| `order_by` | Fields it may sort by | `allow.read()` or `allow.sort()` |
| `limit` | Up to `max_records`, 100 by default | The policy's `max_records`, if lower |
| `records` | Fields the SDK can write, other than formula, lookup, summary, built-in, and user fields | `allow.insert()` or `allow.update()` |
| `report` | Table reports, or summary and chart reports, that can run through the API, by ID. `run()` also takes an ID as a number or a report's exact name, ignoring case, and says which operation runs a report of the other kind | `allow.report()`, and `reports=` |
| `field`, for files | File fields | `allow.download()` or `allow.delete_file()` |

`fields=` narrows all of these to the fields you list. An operation that the client can't run at all,
such as `insert` on a read-only client, raises `PolicyRefusedError` when you make it, rather than
giving the model a tool that always fails. Each list comes from `BoundPolicy.offers()`, which asks the
policy about one field and verb at a time, so what's offered is exactly what's allowed. A rule of your
own is asked too, so one that decides each field on its own is offered exactly what it allows.

### Writing values from JSON

`run()` turns each value into what the write takes, by the field's type, and refuses anything else:

| Field type | The model sends |
|---|---|
| Text, email, URL, phone, predecessor | Text. A multiple-choice field that doesn't allow new choices offers only its choices |
| Number, currency, rating | A number |
| Percent | A fraction, so 50% is `0.5`, in writes and filters alike |
| Duration | Whole milliseconds, so an hour is `3600000`, in writes and filters alike. A fraction is refused, as Quickbase would drop it |
| Checkbox | `true` or `false` |
| Date | `YYYY-MM-DD`, so `today` is refused |
| Timestamp | ISO 8601 with a time zone, like `2026-12-01T09:30:00Z` |
| Time of day | `HH:MM` or `HH:MM:SS` |
| Multi-select text | A list of text |
| File | `{"file_name": ..., "text": ...}`, which uploads the text as a new version |

`null` clears a field, except a file field, as Quickbase ignores an empty file value. `max_rows`, 100 by
default, limits how many records one `insert` or `update` may send.

### Limiting it to a scope

Pass `scope` to `table.operation()` for a condition your code fixes that stays the same, such as one
person's tool, or to `run()` for one that changes with each call, such as the user's project. Given to
both, both apply. Only a scope given to `operation()` can be described to the model: the `where`
description says that every record the tool reaches matches it, such as `Billable is true`, a write's
schema leaves its fields out and says what new records get, and a write that sets one is refused with a
message saying the scope fixes it. Without that, a model can't tell why it may not set a field, and may
tell the person something untrue about it. `operation()` also checks an `insert` scope when it's made,
rather than on the first insert: it must be only `==` conditions, whose fields the client's write rules let
it set.

Either way, the scope is added to every filter, so `{"any": [A, B]}` from the model becomes
`scope AND (A OR B)`.

An operation that names records, such as `update`, `run_formula`, or `download_file`, first counts how
many of them match the scope, with one query, and refuses the whole call if any don't. A record can still
change between that check and the call.

A write can't set a field the scope uses, so `update` can't move a record out of it, and `run()` raises
`QueryError` for a row that tries. `insert` fills those fields in from the scope, so with
`scope=F("Project") == project`, every new record gets that project. That needs a scope of only `==`
conditions joined with `&`, and `insert` refuses any other scope, as a new record couldn't be made to
match it. With a scope passed only to `run()`, pass `fields=` without the scope's fields, so the model
isn't offered them at all. A report runs with its own filter, so it can't take a scope, and `operation()`
and `run()` refuse one rather than ignore it. Under a policy, fields in a scope must be ones the client
may filter on.

### Reports, formulas, and files

- **Reports** are offered by ID, with their names in the schema's description, as names can repeat. Pass
  `reports=` to choose them, as a list of IDs or as `{ID: description}`. A report's columns, filter, and
  sort are its own, so check what it shows before offering it.
- **A formula's text is fixed by your code,** with `formula=`, and the model only chooses the record. A
  formula can read any table and field, and return the token owner's email, so a model never writes one.
  A client with a policy refuses formulas, unless it's `Policy.read_only()`.
- **A downloaded file** comes back as a `FileDownload`, and `tool_result()` gives the model its text if
  it's UTF-8 text. Its contents are untrusted, like any record data.

### Describing fields

`descriptions` takes `{label or ID: text}`, and the schema shows each one where the model reads it: on
each field of a record, and as a list after the description of `where`, `select`, and `order_by`. Each must
be for a field the operation offers, so a field that's renamed or hidden raises `UnknownFieldError`
instead of losing its description silently. `where_schema()` takes the same argument.

For a tool that lists what's there, `tool_result()` takes table, field, and report metadata, without
`raw`:

```python
async def list_fields(table_id: str) -> dict:
    return tool_result(await app.table(table_id).fields())
```

### When something is refused

| What happened | Exception | What's left behind |
|---|---|---|
| The client can't run the operation at all, such as `insert` when read-only | `PolicyRefusedError`, from `operation()` | Nothing was sent |
| The table has nothing for it, such as no file fields, or `formula` is missing | `ConfigurationError`, from `operation()` | Nothing was sent |
| `fields` or `descriptions` names a field the client can't see, or that the operation doesn't offer | `UnknownFieldError`, from `operation()` | Nothing was sent |
| An argument the schema doesn't allow, such as an unknown key or field, or a `limit` too high | `QueryError`, from `run()` | Nothing was sent |
| A value that can't be written, such as `today` for a date | `InvalidValueError`, from `run()` | Nothing was sent |
| A record named isn't in the scope, or doesn't exist | `QueryError`, from `run()` | Only the query that checks the scope |
| A write sets a field the scope uses, or `insert` has a scope it can't fill in | `QueryError`, from `run()` | Nothing was sent |
| A delete matches more than `max_records` | `QueryError`, from `run()` | Only the query that counts it |
| Anything the table method raises | The same exception | As that method says |

Return each one with `tool_error()`. Its message says where the mistake is, and doesn't repeat the values
sent.

### What operations don't do

- **They don't choose your tools.** Give the model a `preview_delete` tool, and run a `delete` only after
  a person approves it, as in [Checking a change before making it](changes.md#checking-a-change-before-making-it).
- **They protect a request's shape, not its meaning.** A filter the schema allows can still match every
  record in the scope.
- **A schema is a snapshot.** It's built from the client's metadata cache when you make the operation.
  `run()` checks against that same snapshot, and the client's limits are checked again on every request,
  so a change in Quickbase can make a call fail, but can't let more through. Make the operation again to
  pick up new fields.
- **Labels and report names are untrusted.** They come from Quickbase, where anyone who can edit them
  can write any text, and the schema shows them to the model.
