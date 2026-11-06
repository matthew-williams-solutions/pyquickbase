# Using pyquickbase from an AI agent

This guide is for developers who let an AI agent use the SDK, and for agents reading these docs.

An agent can write a query or a delete from text it didn't choose, such as a user's message or a
record it just read. The raw REST API accepts many of the resulting mistakes without an error.
`DELETE /records` treats a lowercase operator as "contains", and a value of `_FID_6` in
`{6.EX.'_FID_6'}` [deleted every record](../api-notes.md#query-pitfalls) in a test table.

The SDK catches those mistakes, but what an agent may do is up to you. The docs are also available
as [llms.txt](../llms.txt), a list of the pages, and [llms-full.txt](../llms-full.txt), all of them
in one file.

## A safe starting point

Give the agent small functions rather than the client. Your code fixes the table, fields, and
operators, and the model only supplies values.

```python
from pyquickbase import F, Quickbase


def open_tasks(project: str) -> list[dict]:
    """A tool for the agent, which only chooses `project`."""
    if not project.strip():
        raise ValueError("A project is required.")
    with Quickbase.from_env(read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        where = (F("Project") == project) & (F("Status") == "Open")
        return [dict(task) for task in tasks.query(where=where, select=["Title"], limit=50)]
```

## Limiting access

Give an agent the narrowest client that does its job. `read_only=True` is the place to start, and it
only sends GET requests, record queries, report runs, and formula runs. If the agent also needs to
write, list exactly what it can change with
[`WriteRules`](clients.md#limiting-writes-by-table-action-and-field). Anything else raises
`WriteRefusedError` and is never sent, which also applies to `qb.request()`.

```python
from pyquickbase import Quickbase, WriteAccess, WriteRules

with Quickbase.from_env() as setup:
    tasks = setup.app("bq1234567").table("Tasks")
    tasks_id = tasks.info().id
    status, notes = tasks.resolve_field_ids(["Status", "Notes"])

# It can add tasks and change these two fields, on this table only. It can't delete anything.
rules = WriteRules({tasks_id: WriteAccess(insert=True, update=[status, notes])})
agent_client = Quickbase.from_env(write_policy=rules)
```

Return a `WriteRefusedError` to the model as the tool's result, with [`tool_error()`](#errors). Its
message says what was refused and why, such as a field that isn't allowed, and doesn't repeat the values
it sent.

You can add rules of your own, such as a limit on how many records one call can change, with a
[custom policy](clients.md#writing-your-own-policy).

### Limiting what it reads

An agent doesn't need to see every field the token can, such as a customer's email, phone number, or
internal notes. List what it may read with
[`ReadRules`](clients.md#limiting-reads-by-table-field-and-number-of-records). The SDK enforces this
itself, so it doesn't depend on the model following its instructions:

```python
from pyquickbase import Quickbase, ReadAccess, ReadRules

with Quickbase.from_env() as setup:
    visits = setup.app("bq1234567").table("Appointments")
    visits_id = visits.info().id
    when, status, technician = visits.resolve_field_ids(["When", "Status", "Technician"])

access = ReadAccess(fields=[when, status, technician], filter=[status, technician], max_records=50)
agent_client = Quickbase.from_env(read_policy=ReadRules({visits_id: access}), read_only=True)
```

To the agent, a field or table that isn't listed looks as if it doesn't exist. `table.fields()` leaves it
out, and naming it raises `UnknownFieldError`. Asking for more than is allowed raises `ReadRefusedError`,
whose message says what to change, such as asking for at most 50 records. Return either one to the model
as the tool's result. Both leave out the values it sent.

Email addresses are left out of user values, and the uploader's details out of file values, unless you
pass `user_emails=True`. Formulas are always refused, because a formula can read any table and field.

### What these limits don't do

- **Read rules complement the token's role, and don't replace it.** Quickbase still enforces the role on
  everything they allow.
- **A field you allow can show one you don't.** A formula, lookup, or summary field can copy a hidden
  value, so check what each allowed field is built from.
- **Filtering and sorting reveal values.** A model that may filter on a field can find out what it holds by
  seeing which records match, even if the field isn't returned, so `filter` and `sort` are separate
  lists.
- **Limits apply per query.** `max_records` stops one query from returning a whole table, but a model
  can still make more queries. `ReadRules` `max_records` is how far paging can reach, counting
  `skip`, while an operation's own `max_records` is the size of each page, so set the rules' number as
  high as you want a model to page.
- **`read_only` promises only that it changes no records.** It still runs reports, which adds to
  their usage count in Quickbase, and formulas.
- **Formulas reach further than the table you run them on.** A formula can read other tables with
  `GetRecord()` and `GetRecords()`, and can return the realm and the token owner's email. Don't run
  formula text from a model or another person, even on a read-only client. See the [warning in
  Formulas](formulas.md#running-a-formula).
- **They can't limit by record.** To keep an agent to certain records, use a role with
  [record-level rules](../api-notes.md#record-level-rules), and [fix the scope](#filters-from-a-model)
  of what it reads in your code.
- **They aren't a sandbox.** They apply to one client. If an agent can run code of its own, it can
  create another client with the same token, call the API directly, or reach inside the client to
  remove them.

## The token is the real boundary

A token has its user's permissions, through their role, so Quickbase enforces that limit whichever
code is running.

- Use a token whose user has a role with only the access the agent needs, and give the token access
  to as few apps as possible. Use a sandbox app while you're trying things out.
- Use `Quickbase.from_env()` rather than putting a token in code or a prompt. This keeps it away
  from the model, but not from code the agent runs in the same environment.
- Temporary tokens are deliberately [not in the SDK](../api-notes.md#temporary-tokens).

Test the role you plan to use, because this has only been partly confirmed. A
[view-only role](../api-notes.md#restricted-users) was refused every write we tried, and fields it
couldn't see were left out. A [role with write access](../api-notes.md#roles-with-write-access) was
held to its limits as well, and a formula can't read a table its
[role has no access to](../api-notes.md#restricted-users). That result is empty, not an error, so an
empty result doesn't mean there's no data.
[Record-level rules](../api-notes.md#record-level-rules) held too, for reads, formulas, and writes, but
they only check a record before a change. A role can move a record it may modify to where it can no
longer see it, and can add a record it won't be able to read.

## Untrusted values

Use [`F`](reading.md#writing-queries) for a value a model or user supplied, or
[`where_from_dict()`](#filters-from-a-model) if they also choose the filter, and never build a `where`
string from it. The SDK can't tell an added condition from one you meant to write, so a
`code` of `zz'}OR{3.GT.'0` makes the second line below [match every
record](../api-notes.md#query-pitfalls):

```python
tasks.delete(F("Code") == code)  # Raises QueryError for that value
tasks.delete(f"{{6.EX.'{code}'}}")  # Don't do this
```

`F` escapes quotes and backslashes, and handles the text that Quickbase would read as query syntax:

- A value containing `{` or `}` raises `QueryError`.
- A value like `_FID_6` raises `QueryError`.
- A standalone uppercase `OR` or `AND` is sent in lowercase, so `a OR b` finds that text rather than
  matching `a` and `b`. Comparisons ignore case, so it still finds a stored `a OR b`.
- Anything other than text, a number, a date, a boolean, or None raises `QueryError`, and so does a
  `datetime` without a timezone.

That protects the shape of a query, not its meaning:

- **Empty values match empty fields.** `F("Code") == ""` and `F("Code") == None` match every record
  with no code, so check that a value isn't empty first, especially before a delete.
- **A model that chooses the field or operator chooses the result.** `F("Status") != "x"` is a valid
  query that matches almost everything. Fix these in your code.
- **`delete()` requires `where`,** so leaving it out can't delete everything, but a broad condition
  still can. Call [`preview_delete()`](#checking-a-change-before-making-it) with the same condition first,
  and compare its `total` with what you expect.

!!! note "`_curuser_` and relative dates"
    On a `user` or `multiuser` field, Quickbase reads `_curuser_` as the token's owner, ignoring case
    and surrounding spaces, so a value from someone else could match the owner's records. `F` raises
    `QueryError` for it there. On a text field it's literal text, so `F` allows it.

    On a date field, Quickbase reads text such as `today` or `1 day ago` as a date. That gives a
    model nothing it couldn't get by naming the date, but text it can't read matches nothing, with no
    error. Convert dates to `date` objects, so a bad one fails right away.

[Try it in the playground](../playground-app/index.html#search-with-text-you-dont-control), with simulated data.

## Filters from a model

When a model needs to choose its own filters, have it send JSON, and don't let it write query strings.
For a tool that runs one table operation, [`table.operation()`](#giving-a-model-operations) does this for
you, with the schema and the check built together. Use these two directly for anything else, such as a
web form's filters.
This works the same with MCP and any other LLM tool system, because they all describe a tool's input
with JSON Schema. `where_schema()` provides that description, and `where_from_dict()` checks what
comes back.

```python
from pyquickbase import F, Quickbase, where_from_dict, where_schema

FILTERABLE = ["Status", "Due Date", "Amount"]

SEARCH_TOOL_INPUT = {
    "type": "object",
    "properties": {"where": where_schema(fields=FILTERABLE)},
    "required": ["where"],
}


def search_tasks(project: str, where: dict) -> list[dict]:
    """The tool. Your code fixes the table, the project, and the fields it may filter on."""
    with Quickbase.from_env(read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        condition = (F("Project") == project) & where_from_dict(where, fields=FILTERABLE)
        return [dict(task) for task in tasks.query(where=condition, select=["Title"], limit=50)]
```

Return a `QueryError` or `UnknownFieldError` to the model as the tool's result, with
[`tool_error()`](#errors). Their messages say where the mistake is and what's allowed, so it can correct
itself, and they don't repeat the values it sent. Nothing has been sent to Quickbase at that point.

`where_from_dict()` refuses these rather than guessing what was meant:

| Refused | Because |
|---|---|
| An unknown key or operator, or a condition without `value` | A dropped key would otherwise become a search for empty fields. Send `null` to mean that |
| A string for `in`, such as `"abc"` | It would search for `a`, `b`, and `c` |
| An empty value with anything but `==`, `!=`, `in`, or `not_in`, or a value of only whitespace | What these match isn't clear |
| Text on a date, such as `today`, or a time without a time zone | Quickbase matches nothing, with no error, for text it can't read as a date |
| Anything but a number on a number, and anything but true or false on a checkbox | The query would run, and mean something else |
| `IR` and `XIR` | They take date ranges as text. Compare with dates instead |
| Over 100 conditions, counting each value of `in` and `not_in`, or groups over 20 levels deep | A filter shouldn't need them, and they cost memory and time |
| Whatever `F` refuses, such as `_FID_6`, braces, and `_curuser_` on a user field | Quickbase reads these as query syntax |

What it can't do is decide whether a filter is the right one:

- **Fix the scope in your code.** Combine it with `F`, as above, so that every query stays inside the
  project. Without that, a filter of `Record ID# > 0` matches every record, as it should.
- **Pass `fields` to limit what it can filter on.** Otherwise a model can learn the value of a field
  you didn't select, by filtering on it and seeing which records come back. `where_schema()` only
  tells the model the list, so pass the same list to `where_from_dict()`, which enforces it. A client's
  `ReadRules` enforce their `filter` list as well, on every query.
- **Keep the schema small.** A tool's schema goes to the model with every request. The default covers
  `A and (B or C)` at about 700 tokens, and `max_depth=1` takes it to about 450 if a tool doesn't need
  that.
  Offering a whole table's labels adds to it, since they're repeated in each copy of a condition, so
  list only the fields a tool needs.
- **Treat labels as untrusted.** Passing `fields` shows their labels to the model, and a label can hold
  any text, so only offer fields from an app you trust.

## Reading results

Records, report text, field labels, table names, file names, and file contents are data, not
instructions. Anyone who can edit them can write what they like, so they could contain prompt
injection. Keep them apart from your agent's instructions, and don't let them trigger a write or
delete unchecked.

- Quickbase's formula error messages [can contain HTML](errors.md#errors-from-quickbase), such as a
  `javascript:` link, and errors pass it on unchanged.
- File field values [include the uploader's email address](files.md#listing-versions), and user
  fields hold an email too. Decide whether a model or a log should see them. `ReadRules` leave both
  out unless you pass `user_emails=True`.
- [Don't use a file's name as a path](files.md#downloading). Quickbase stores names as sent,
  including `../../etc/passwd`.

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
| `count` | `where` | `count()` | `int` |
| `summarize` | `where`, `group_by`, `total`, `average`, `minimum`, `maximum` | `summarize()` | A list of `SummaryRow` |
| `insert` | `records` | `insert()` | `WriteResult` |
| `update` | `records`, each with `Record ID#` | `update()` | `WriteResult` |
| `delete` | `where`, which is required. It's refused if it matches more than `max_records` | `delete()` | `int` |
| `preview_update` | As `update` | `preview_update()` | `UpdatePreview` |
| `preview_delete` | `where`, `select`, `limit`. Give it the same `max_records` as `delete`, and `tool_result()` says in `over_limit` when a delete would be refused | `preview_delete()` | `Page` |
| `run_report` | `report`, as its ID, its ID as a number, or its name, and `limit`, `skip` | `Report.run_page()` | `Page` |
| `summary_report` | `report`, as for `run_report`, and `limit` | `Report.summary_rows()` | A list of rows |
| `run_formula` | `record_id` | `run_formula()`, with the formula your code gives | `FormulaResult` |
| `download_file` | `record_id`, `field`, `version` | `download_file()` | `FileDownload` |
| `delete_file_version` | `record_id`, `field`, `version` | `delete_file_version()` | None |

`where` is the format of [`where_from_dict()`](#filters-from-a-model). `order_by` is a list of
`{"field": ..., "descending": true or false}`, and `group_by` takes labels, or
`{"field": ..., "unit": "month"}` to group a date. Whatever `run()` returns, `tool_result()` takes.

### What a schema offers

The schema lists only what the client may do, so a model isn't offered a field that would be refused:

| Part | Offers | Limited by |
|---|---|---|
| `where` | Fields it may filter on | `ReadRules` `filter` |
| `select`, `group_by`, totals | Fields it may return | `ReadRules` `fields` |
| `order_by` | Fields it may sort by | `ReadRules` `sort` |
| `limit` | Up to `max_records`, 100 by default | `ReadRules` `max_records`, if lower |
| `records` | Fields the SDK can write, other than formula, lookup, summary, built-in, and user fields | `WriteRules` `insert` or `update` |
| `report` | Table reports, or summary and chart reports, that can run through the API, by ID. `run()` also takes an ID as a number or a report's exact name, ignoring case, and says which operation runs a report of the other kind | `ReadRules` `reports` or `summary_reports`, and `reports=` |
| `field`, for files | File fields | `ReadRules` `files`, or `WriteRules` `delete_files` |

`fields=` narrows all of these to the fields you list. An operation that the client can't run at all,
such as `insert` on a `read_only` client, raises `WriteRefusedError` or `ReadRefusedError` when you make
it, rather than giving the model a tool that always fails. Custom policy functions aren't consulted, as
they can't be asked about a field without a request, so one may refuse more than the schema offers.

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
and `run()` refuse one rather than ignore it. With `ReadRules`, fields in a scope must be ones the client
may filter on.

### Reports, formulas, and files

- **Reports** are offered by ID, with their names in the schema's description, as names can repeat. Pass
  `reports=` to choose them, as a list of IDs or as `{ID: description}`. A report's columns, filter, and
  sort are its own, so check what it shows before offering it.
- **A formula's text is fixed by your code,** with `formula=`, and the model only chooses the record. A
  formula can read any table and field, and return the token owner's email, so a model never writes one.
  A client with `ReadRules` refuses formulas.
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
| The client can't run the operation at all, such as `insert` when read-only | `WriteRefusedError` or `ReadRefusedError`, from `operation()` | Nothing was sent |
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
  a person approves it, as in [Checking a change before making it](#checking-a-change-before-making-it).
- **They protect a request's shape, not its meaning.** A filter the schema allows can still match every
  record in the scope.
- **A schema is a snapshot.** It's built from the client's metadata cache when you make the operation.
  `run()` checks against that same snapshot, and the client's limits are checked again on every request,
  so a change in Quickbase can make a call fail, but can't let more through. Make the operation again to
  pick up new fields.
- **Labels and report names are untrusted.** They come from Quickbase, where anyone who can edit them
  can write any text, and the schema shows them to the model.

## Returning results to a model

A tool's result goes back to the model as JSON. `tool_result()` and `tool_error()` turn the SDK's
results and errors into plain data in the same shapes every time, so a model can tell whether it has every
record, what went wrong, and whether anything changed. They work with any tool-calling framework, as
`json.dumps()` always accepts what they return, and they don't need the network.

```python
from pyquickbase import F, Quickbase, QuickbaseError, tool_error, tool_result


def open_tasks(project: str, skip: int = 0) -> dict:
    """A tool for the agent, which chooses the project and which page to read."""
    try:
        with Quickbase.from_env(read_only=True) as qb:
            tasks = qb.app("bq1234567").table("Tasks")
            where = (F("Project") == project) & (F("Status") == "Open")
            page = tasks.query_page(where=where, select=["Title", "Due Date"], top=50, skip=skip)
        return tool_result(page, compact=True)
    except QuickbaseError as error:
        return tool_error(error)
```

### What a result holds

Every result has `"ok": true`, and a key for what it holds:

| You pass | It holds |
|---|---|
| A `Page` or `TypedPage`, from `query_page()` | `records`, or `columns` and `rows` with `compact=True`, and `page` |
| A list of records or dictionaries, such as from `query()` or `summary_rows()` | The same, with `page.total` as null, as it isn't known |
| A `Record`, `TypedRecord`, or None, from `get()` or `first()` | `record`, which is null for None |
| A `WriteResult` | `created_ids`, `updated_ids`, and `unchanged_ids` |
| A list of `SummaryRow`, from `summarize()` | `groups`, each with `group`, `count`, and the totals it has |
| An `UpdatePreview` | `changes`, with `before` and `after` for each field, `unchanged_ids`, and `missing_ids` |
| A `FormulaResult` | `value` and `type` |
| Text, a number, a date, or a bool, such as from `count()` | `value` |

Records are keyed by field label, or by attribute name for a generated class. Values are written the same
way whatever their field:

- A date is `2027-01-15`, and a timestamp is in UTC, as `2027-01-15T14:30:00Z`.
- Every kind of empty value is null. Quickbase returns `""` for an empty text field, null for an empty
  number, and `{"url": "", "versions": []}` for an empty file, so a model would otherwise have to know each.
- A `Decimal` is a number when that's exact, and text when a number would lose digits.
- A user is `{"id", "name", "email"}`, with a null email when a client's
  [read rules](#limiting-what-it-reads) leave it out. A file is
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
- **Pass `max_records`** to cut a list your code already has. `page.has_more` is then true, so the model
  knows it doesn't have them all.
- **Pass `max_text`** to shorten long text, such as notes. A shortened value ends by saying how many
  characters were left out, and the result has `"text_shortened": true`, so a model can't mistake it for
  the whole value.
- **Select only the fields the tool needs.** Every field is in every record.

### Errors

Every error has `"ok": false`, and an `error` with these keys:

| Key | What it holds |
|---|---|
| `code` | What went wrong, as a fixed word. Codes don't change within 1.x, unlike the wording of `message` |
| `message` | The SDK's explanation, which says where the mistake is. It doesn't repeat the values a model sent |
| `hint` | What the model should do next, in fixed words for each code |
| `retryable` | Whether the same call can succeed later, without changing it |
| `changed` | `none` if nothing was changed, `some` if part of a write was, and `unknown` if a write may have been |
| `retry_after` | For a rate limit, the seconds Quickbase asked to wait, when it said |
| `status` | For an error from Quickbase, the HTTP status |
| `written`, `failed_rows` | For a write that failed, the IDs that were created, updated, and left unchanged, and each failed row's `index`, `kind`, `field_id`, and `messages` |

```json
{
  "ok": false,
  "error": {
    "code": "partial_write",
    "message": "1 of 3 rows failed ...",
    "hint": "Some records were changed. Fix the failed rows and send only those again.",
    "retryable": false,
    "changed": "some",
    "written": {"created_ids": [4, 5], "updated_ids": [], "unchanged_ids": []},
    "failed_rows": [{"index": 2, "kind": "duplicate", "field_id": 10, "messages": ["..."]}]
  }
}
```

`changed` is what tells a partial write from a complete failure. A model should check the data before
repeating a call whose `changed` isn't `none`, as repeating an insert can add duplicates.

| Code | Raised for | `changed` | `retryable` |
|---|---|---|---|
| `invalid_query`, `invalid_value` | A filter, argument, or value that can't be sent | `none` | No |
| `unknown_table`, `unknown_field`, `unknown_report` | A name that doesn't exist, or that the token or the read rules hide | `none` | No |
| `unsupported_report` | A report that can't be read that way | `none` | No |
| `read_refused`, `write_refused`, `refused` | The client's limits refused it, and it wasn't sent | `none` | No |
| `write_failed` | Every row of a write failed | `none` | No |
| `partial_write` | Some rows were written and some weren't, or a later batch failed | `some`, or `unknown` if the failed batch may have been written | No |
| `write_permission` | The token's role refused a write | `none`, or `some` if earlier rows were written | No |
| `bad_request`, `not_found`, `permission_denied`, `authentication`, `api_error` | Quickbase answered with an error | `none` | No |
| `rate_limited` | Quickbase is limiting requests, and the retries ran out | `none` | Yes |
| `server_error`, `timeout`, `network` | Quickbase failed, or didn't answer | `unknown` | Yes |
| `invalid_response` | Quickbase's answer couldn't be read | `unknown` | No |
| `schema_mismatch`, `configuration` | The tool's setup no longer matches, which the model can't fix | `none` | No |
| `error` | Any other error from the SDK | `none` | No |
| `internal` | An exception from outside the SDK, such as a bug in the tool | `unknown` | No |

An exception from outside the SDK gets the message "An unexpected error occurred in the tool.", so a
bug's details, and any values in them, don't reach the model. Log it yourself before returning it.

### What these refuse

| What happened | Exception | What's left behind |
|---|---|---|
| `tool_result()` got something it can't describe, such as a `FileDownload`, a `TableInfo`, or a dictionary | `TypeError` | Nothing was returned |
| A record holds a value of a type JSON can't hold, such as `bytes` | `TypeError` | Nothing was returned |
| `max_records` or `max_text` isn't a positive whole number | `ValueError` | Nothing was returned |

### What these don't do

- **They don't make record data safe.** Labels and values are still [untrusted](#reading-results), so a
  record can hold text meant to steer the model. Keep results apart from your instructions.
- **They don't hide fields.** They return what you give them, so select only what the model should see, or
  use [read rules](#limiting-what-it-reads), which the SDK enforces.
- **Quickbase's own wording passes through.** An error from Quickbase keeps its message, up to 500
  characters, because a model needs it to fix a request Quickbase refused, such as `Criteria is invalid`.
  A row's `messages` are Quickbase's too. They can contain HTML, and may repeat what was sent.
- **An empty list reads as no records,** even if it came from `summarize()`, as nothing in an empty list
  says what it held.

## Linking to Quickbase

A tool can return links to what a model read or changed, so that a person can check it in the
Quickbase interface. Each method looks up the IDs it needs from the client's cache, as any other
method does, and puts only those IDs in the link, never a field's value. A table, field, or report
that the client's `ReadRules` hide is refused as unknown.

```python
from pyquickbase import AsyncTable, F, tool_result


async def open_tasks(tasks: AsyncTable) -> dict:
    page = await tasks.query_page(where=F("Status") == "Open", top=50)
    # https://<realm>/nav/app/<app ID>/table/<table ID>/action/dr?rid={Record ID#}
    pattern = await tasks.record_url_pattern()
    return {**tool_result(page), "record_link": pattern}
```

Send the pattern once, rather than a link for each record, and say in the tool's description that
`{Record ID#}` is replaced by a record's `Record ID#`. [`record_url()`](../reference/table.md#links)
builds one record's link, `url()` a table's, app's, or report's, and `field_url()` the page that
edits a field. To show which records a query asked for, `compile_where()` returns a condition as
Quickbase's query text, such as `{7.EX.'Open'}`.

## Counts and totals

Don't have a model count or add up records itself, because it can miss some or get the arithmetic wrong. Give
it a tool that calls [`summarize()`](reading.md#summarizing-records), and always set `max_records`, so that a
broad request can't read a whole large table. If the limit is exceeded, nothing is returned but the error, so
a model can't report a total from only part of the data.

```python
from pyquickbase import F, Quickbase, QuickbaseError, tool_error, tool_result, where_from_dict


def task_totals(project: str, where: dict | None = None) -> dict:
    """A tool for the agent, which can only choose the project and a filter on Status."""
    try:
        condition = F("Project") == project
        if where:
            condition = condition & where_from_dict(where, fields=["Status"])
        with Quickbase.from_env(read_only=True) as qb:
            tasks = qb.app("bq1234567").table("Tasks")
            rows = tasks.summarize(
                group_by="Status", where=condition, total="Amount", max_records=5000
            )
        return tool_result(rows)
    except QuickbaseError as error:
        return tool_error(error)
```

`tool_result()` writes dates in `minimum` and `maximum` as text, as it does everywhere. A `max_records`
that's exceeded is an `invalid_query`, whose message says how many records matched.

## Checking a change before making it

### Requiring the preview

Telling a model to preview first isn't enough: in testing, a model updated a record without a preview,
or any approval. With `require_preview=True`, the `update` and `delete` operations only run with the
`confirmation` that `preview_update` or `preview_delete` returned, for exactly the same arguments:

```python
preview = await tasks.operation("preview_update")
update = await tasks.operation("update", require_preview=True)

shown = tool_result(await preview.run(arguments))  # holds "confirmation"
# ... the person approves what the preview showed ...
await update.run({**arguments, "confirmation": shown["confirmation"]})
```

- **The arguments must match.** The order of records, and of fields in a record, doesn't matter, but
  any other difference refuses the call, so a model can't preview one change and make another.
- **A confirmation lasts 15 minutes.**
- **A delete's confirmation holds how many records matched**, so the delete is refused if that has
  changed since the preview. A preview of more records than the delete may match gives no confirmation.
- **It's signed with a key each client makes when it starts**, so nothing is stored, a model can't make
  one up, and the preview and the change must come from the same client. Give them the same `scope`.

It makes sure a preview happened, not that a person saw it. Ask your client to show each change for
approval too.

### Previews without operations

Have a tool show what it would do, and make the change only once someone has approved it. A preview only
reads, so the tool that shows it can use a read-only client, and only the tool that writes needs more.

```python
from pyquickbase import F, Quickbase, tool_result, where_from_dict


def preview_status_change(record_id: int, status: str) -> dict:
    """The tool the model calls first. It changes nothing."""
    with Quickbase.from_env(read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = tasks.preview_update([{"Record ID#": record_id, "Status": status}])
    return tool_result(preview)


def preview_delete_tasks(project: str, where: dict) -> dict:
    """Shows how many tasks a delete would remove, and the first few."""
    with Quickbase.from_env(read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        condition = (F("Project") == project) & where_from_dict(where, fields=["Status"])
        page = tasks.preview_delete(condition, select=["Title"], top=10)
    return tool_result(page)
```

The delete preview's `page.total` is how many tasks the delete would remove. Add `tool_error()` as in
[Returning results to a model](#returning-results-to-a-model) to return a refusal too.

The values in a preview are record data, so treat them as [untrusted](#reading-results), and only return
fields the model should see. A preview isn't a guarantee, as records can change before the write, and
[it doesn't check](writing.md#previewing-a-write) whether Quickbase will accept it. Compare a delete's
result with the preview's `total`, and tell the user if they differ.

## Writes

Quickbase writes the rows it can and reports the rest, so a failed write can still have changed
data. Check `WriteError`, which [lists both](writing.md#when-rows-fail):

```python
from pyquickbase import WriteError

try:
    result = tasks.insert(rows)
except WriteError as error:
    print("Written:", error.result.created_ids)
    for failed in error.result.errors:
        print("Failed:", rows[failed.index], failed.messages)
```

Rate limits, connection failures, and reads that timed out are retried for you. A write that timed out
isn't, because it
may already have applied, so an agent should check the data before trying again.

| Exception | What it means for a write |
|---|---|
| [Mistakes caught before sending](errors.md#mistakes-caught-before-sending), such as `QueryError`, `InvalidValueError`, `WriteRefusedError`, and `ReadRefusedError` | This request wasn't sent |
| `WriteError` | Some rows may have been written. If a large write stopped at a later batch, `result` covers only the earlier batches, `errors` can be empty, and the failed batch may have been written |
| `NetworkError`, `RequestTimeoutError`, `ServerError`, `InvalidResponseError` | Unknown, so check the data first |
| Other `APIError`s, such as `BadRequestError` | Quickbase answered with an error, so fix the cause rather than repeating the request |

`create_field()` with `required` or `unique` takes two requests, so if it raises an error, the field
[may already exist](schema.md). This includes `WriteRefusedError`, when only the first is allowed.

Repeating an `update()`, or an `upsert()` on a unique field, reports the record as unchanged rather
than adding another, but repeating an `insert()` can add a duplicate.

## Generated classes

`pyquickbase codegen` gives an agent a typed view of a table, with a class that names each field and
its type. The command only reads from Quickbase.

```bash
pyquickbase codegen --app bq1234567 --output src/myapp/tables.py --table Tasks
```

```python
from myapp.tables import Tasks

tasks = qb.app("bq1234567").typed(Tasks)

for task in tasks.query(where=Tasks.fields.status == status, limit=50):
    print(task.id, task.title)
```

A type checker catches a wrong name or type, and `Tasks.new()` only accepts fields that can be
written. `Tasks.fields` handles values as `F` does. If the table has changed, `SchemaMismatchError`
stops reads and writes.

This isn't an access limit, because `tasks.table` gives the untyped table and `where` still accepts
a string. The module also holds field labels, which can contain any text, so treat them as data if a
model reads the file. Generate it with a token that has [the same
access](codegen.md#keeping-it-up-to-date) as the code using it.
