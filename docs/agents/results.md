# Results and errors for a model

## Reading results

Records, report text, field labels, table names, file names, and file contents are data, not
instructions. Anyone who can edit them can write what they like, so they could contain prompt
injection. Keep them apart from your agent's instructions, and don't let them trigger a write or
delete unchecked.

- Quickbase's formula error messages [can contain HTML](../guides/errors.md#errors-from-quickbase), such as a
  `javascript:` link, and errors pass it on unchanged.
- File field values [include the uploader's email address](../guides/files.md#listing-versions), and user
  fields hold an email too. Decide whether a model or a log should see them. A policy leaves both
  out unless a rule that reads the table passes `user_emails=True`.
- [Don't use a file's name as a path](../guides/files.md#downloading). Quickbase stores names as sent,
  including `../../etc/passwd`.

## Returning results to a model

A tool's result goes back to the model as JSON. `tool_result()` and `tool_error()` turn the SDK's
results and errors into plain data in the same shapes every time, so a model can tell whether it has every
record, what went wrong, and whether anything changed. They work with any tool-calling framework, as
`json.dumps()` always accepts what they return, and they don't need the network.

```python
from pyquickbase import F, Policy, Quickbase, QuickbaseError, tool_error, tool_result


def open_tasks(project: str, skip: int = 0) -> dict:
    """A tool for the agent, which chooses the project and which page to read."""
    try:
        with Quickbase.from_env(policy=Policy.read_only()) as qb:
            tasks = qb.app("bq1234567").table("Tasks")
            where = (F("Project") == project) & (F("Status") == "Open")
            page = tasks.query_page(where=where, select=["Title", "Due Date"], limit=50, skip=skip)
        return tool_result(page, compact=True)
    except QuickbaseError as error:
        return tool_error(error)
```

### What a result holds

Every result has `"ok": true`, and a key for what it holds:

| You pass | It holds |
|---|---|
| A `RecordPage`, from `query_page()` | `records`, or `columns` and `rows` with `compact=True`, and `page` |
| A list of records or dictionaries, such as from `query()` or `run_summary()` | The same, with `page.total` as null, as it isn't known |
| A `Record` or None, from `get()` or `first()` | `record`, which is null for None |
| A `WriteResult` | `created_ids`, `updated_ids`, and `unchanged_ids` |
| A list of `SummaryRow`, from `summarize()` | `groups`, each with `group`, `count`, and the totals it has |
| An `UpdatePreview` | `changes`, with `before` and `after` for each field, `unchanged_ids`, and `missing_ids` |
| A `FormulaResult` | `value` and `type` |
| A `count` operation's result | `count` |
| A `delete` operation's result | `deleted_count` |
| Text, a number, a date, or a bool, such as from `count()` | `value` |

Records are keyed by field label. Values are written the same way whatever their field:

- A date is `2027-01-15`, and a timestamp is in UTC, as `2027-01-15T14:30:00Z`.
- Every kind of empty value is null. Quickbase returns `""` for an empty text field, null for an empty
  number, and `{"url": "", "versions": []}` for an empty file, so a model would otherwise have to know each.
- A `Decimal` is a number when that's exact, and text when a number would lose digits.
- A user is `{"id", "name", "email"}`, with a null email when a client's
  [read rules](index.md#limiting-access) leave it out. A file is
  `{"url", "versions": [{"number", "file_name", "uploaded", "creator"}]}`, or with `compact`, the latest
  file's name.

### Paging and long results

`page` says how much of the result the model has:

```json
{"returned": 50, "total": 1234, "skip": 0, "next_skip": 50, "has_more": true}
```

Give the tool a `skip` argument, as above, so the model can ask for the next page with `next_skip`. Use
`query_page()` rather than `query()`, as only a page knows its `total`.

Results cost tokens, so return as little as the model needs:

- **Pass `compact=True`** when a tool returns many records. Each label is sent once, as `columns`, and each
  record is a list of values in `rows`, rather than repeating every label on every record. A file value
  becomes the latest file's name. For 50 records of three short fields, it's about half the size. The
  saving is larger when labels are long and values short.
- **Pass `max_chars`** so that one call can't fill the model's context, however wide the table or long
  its text. Records and summary groups are included from the first until the next wouldn't fit, then
  `page.has_more` is true and `page.next_skip` says where to carry on, so paging still reaches every one.
  At least one is always included, with its text cut shorter if it's too large on its own. 32,000, about
  8,000 tokens, suits most tools.
- **Pass `max_records`** to cut a list your code already has. `page.has_more` is then true, so the model
  knows it doesn't have them all.
- **Pass `max_text_chars`** to shorten long text, such as notes. A shortened value ends by saying how many
  characters were left out, and the result has `"text_truncated": true`, so a model can't mistake it for
  the whole value.
- **Select only the fields the tool needs.** Every field is in every record.

### Errors

Every error has `"ok": false`, and an `error` with these keys:

| Key | What it holds |
|---|---|
| `code` | What went wrong, as a fixed word. Codes don't change within 1.x, unlike the wording of `message` |
| `message` | The SDK's explanation, which says where the mistake is. It doesn't repeat the values a model sent |
| `hint` | What the model should do next, in fixed words for each code. A failed schema change has its own, as it has no failed rows, and so does a `partial_write` whose `data_changed` is `unknown`, as the failed batch may have been written |
| `retryable` | Whether the same call can succeed later, without changing it |
| `data_changed` | `no` if nothing was changed, `partly` if part of a write was, and `unknown` if a write may have been |
| `retry_after` | For a rate limit, the seconds Quickbase asked to wait, when it said |
| `status` | For an error from Quickbase, the HTTP status |
| `written`, `failed_rows` | For a write that failed, the IDs that were created, updated, and left unchanged, and each failed row's `index`, `reason`, `field_id`, and `messages` |

```json
{
  "ok": false,
  "error": {
    "code": "partial_write",
    "message": "1 of 3 rows failed ...",
    "hint": "Some records were changed. Fix the failed rows and send only those again.",
    "retryable": false,
    "data_changed": "partly",
    "written": {"created_ids": [4, 5], "updated_ids": [], "unchanged_ids": []},
    "failed_rows": [{"index": 2, "reason": "duplicate", "field_id": 10, "messages": ["..."]}]
  }
}
```

`data_changed` is what tells a partial write from a complete failure. A model should check the data
before repeating a call whose `data_changed` isn't `no`, as repeating an insert can add duplicates.

A model can decide what to do next from `retryable`, `data_changed`, and `code` alone, without
reading the message. Check these in order, and act on the first that applies:

| If | Then |
|---|---|
| `retryable` is true | Wait `retry_after` if it's given, then send it again. If `data_changed` is `unknown`, check the data first |
| `code` is `write_permission_denied` | Stop, and tell the user what was written |
| `code` is `internal` | Stop, and tell the user |
| `data_changed` is `unknown` | Check what was written, then send only what's missing |
| `data_changed` is `partly`, or `code` is `write_failed` | Fix `failed_rows`, and send only those. For a schema change, preview it again |
| `code` is `needs_confirmation` | Preview again, show the person, then confirm |
| `code` is `summary_too_large` | Count without totals, which works at any size, or ask the user to narrow the question |
| `code` is `invalid_arguments`, `invalid_value`, `unknown_table`, `unknown_field`, `unknown_report`, `unsupported_report`, `not_found`, `bad_request`, or `read_refused` | Fix the call, and try again |
| Anything else | Stop, and tell the user |

The order matters: a `write_permission_denied` can be `partly`, and an `internal` error is always
`unknown`, but neither is worth sending again.

| Code | Raised for | `data_changed` | `retryable` |
|---|---|---|---|
| `needs_confirmation` | A change without the confirmation from its preview or plan, or with one that's expired or for something else | `no` | No |
| `invalid_arguments`, `invalid_value` | A filter, argument, or value that can't be sent | `no` | No |
| `summary_too_large` | A summary that would read more records than its `max_records_scanned`, with `matched` and `limit` | `no` | No |
| `unknown_table`, `unknown_field`, `unknown_report` | A name that doesn't exist, or that the token or the read rules hide | `no` | No |
| `unsupported_report` | A report that can't be read that way | `no` | No |
| `read_refused`, `write_refused` | The client's limits refused it, and it wasn't sent | `no` | No |
| `write_failed` | Every row of a write failed | `no` | No |
| `partial_write` | Some rows were written and some weren't, or a later batch failed | `partly`, or `unknown` if the failed batch may have been written | No |
| `write_permission_denied` | The token's role refused a write | `no`, or `partly` if earlier rows were written | No |
| `bad_request`, `not_found`, `permission_denied`, `authentication`, `api_error`, `no_longer_available`, `code_page_changed` | Quickbase answered with an error | `no` | No |
| `rate_limited` | Quickbase is limiting requests, and the retries ran out | `no` | Yes |
| `server_error`, `timeout`, `network_error` | Quickbase failed, or didn't answer | `unknown` | Yes |
| `invalid_response` | Quickbase's answer couldn't be read | `unknown` | No |
| `schema_mismatch`, `misconfigured` | The tool's setup no longer matches, which the model can't fix | `no` | No |
| `quickbase_error` | Any other error from the SDK | `no` | No |
| `internal` | An exception from outside the SDK, such as a bug in the tool | `unknown` | No |

An exception from outside the SDK gets the message "An unexpected error occurred in the tool.", so a
bug's details, and any values in them, don't reach the model. Log it yourself before returning it.

### What these refuse

| What happened | Exception | What's left behind |
|---|---|---|
| `tool_result()` got something it can't describe, such as an `AppInfo`, a dictionary, or a set | `TypeError` | Nothing was returned |
| A record holds a value of a type JSON can't hold, such as `bytes` | `TypeError` | Nothing was returned |
| `max_records`, `max_text_chars`, or `max_chars` isn't a positive whole number | `ValueError` | Nothing was returned |

### What these don't do

- **They don't make record data safe.** Labels and values are still [untrusted](#reading-results), so a
  record can hold text meant to steer the model. Keep results apart from your instructions.
- **They don't hide fields.** They return what you give them, so select only what the model should see, or
  use [read rules](index.md#limiting-access), which the SDK enforces.
- **Quickbase's own wording passes through.** An error from Quickbase keeps its message, up to 500
  characters, because a model needs it to fix a request Quickbase refused, such as `Criteria is invalid`.
  A row's `messages` are Quickbase's too. They can contain HTML, and may repeat what was sent.
- **An empty list reads as no records,** even if it came from `summarize()`, as nothing in an empty list
  says what it held.

## Linking to Quickbase

A tool can return links to what a model read or changed, so that a person can check it in the
Quickbase interface. Each method looks up the IDs it needs from the client's cache, as any other
method does, and puts only those IDs in the link, never a field's value. A table, field, or report
that the client's policy hides is refused as unknown.

```python
from pyquickbase import AsyncTable, F, tool_result


async def list_open_tasks(tasks: AsyncTable) -> dict:
    page = await tasks.query_page(where=F("Status") == "Open", limit=50)
    # https://<realm>/nav/app/<app ID>/table/<table ID>/action/dr?rid={record_id}
    pattern = await tasks.record_url_template()
    return {**tool_result(page), "record_link": pattern}
```

Send the pattern once, rather than a link for each record, and say in the tool's description that
`{record_id}` is replaced by a record's `Record ID#`. [`record_url()`](../reference/table.md#links)
builds one record's link, and `url()` a table's, app's, or report's. Others link to the pages a
change to the schema sends a person to, such as `field_url()` and `fields_url()` for a table's
fields, `relationships_url()`, and `Report.url(edit=True)` for a report's settings. To show which records a query asked for, `compile_where()` returns a condition as
Quickbase's query text, such as `{7.EX.'Open'}`.

## Counts and totals

Don't have a model count or add up records itself, because it can miss some or get the arithmetic wrong. Give
it a tool that calls [`summarize()`](../guides/reading.md#summarizing-records), and always set `max_records_scanned`, so that a
broad request can't read a whole large table. If the limit is exceeded, nothing is returned but the error, so
a model can't report a total from only part of the data.

```python
from pyquickbase import (
    F,
    Policy,
    Quickbase,
    QuickbaseError,
    tool_error,
    tool_result,
    where_from_dict,
)


def task_totals(project: str, where: dict | None = None) -> dict:
    """A tool for the agent, which can only choose the project and a filter on Status."""
    try:
        condition = F("Project") == project
        if where:
            condition = condition & where_from_dict(where, fields=["Status"])
        with Quickbase.from_env(policy=Policy.read_only()) as qb:
            tasks = qb.app("bq1234567").table("Tasks")
            rows = tasks.summarize(
                group_by="Status", where=condition, sum="Amount", max_records_scanned=5000
            )
        return tool_result(rows)
    except QuickbaseError as error:
        return tool_error(error)
```

`tool_result()` writes dates in `min` and `max` as text, as it does everywhere. A `max_records_scanned`
that's exceeded is a `summary_too_large`, with `matched` and `limit`. A summary that only counts reads no
records, so it's never refused this way.
