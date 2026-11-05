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

`read_only=True` sends only GET requests, record queries, report runs, and formula runs. To allow
specific writes as well, pass `allowed_writes`, whose paths must match exactly. See [Limiting what a
client can do](clients.md#limiting-what-a-client-can-do).

```python
qb = Quickbase.from_env(allowed_writes={("POST", "/records")})
```

Both limits also apply to `qb.request()`. Anything else raises `ReadOnlyError` and is never sent,
although a table method may read the table's metadata before getting that far.

### What these limits don't do

- **They aren't per-table boundaries.** `allowed_writes={("POST", "/records")}` allows that request
  for any table the token can reach, because the table is in the request's body. The same request
  also covers `insert()`, `update()`, `upsert()`, and file uploads.
- **They don't limit reads.** These clients can read whatever the token can.
- **`read_only` promises only that it changes no records.** It still runs reports, which adds to
  their usage count in Quickbase, and formulas.
- **Formulas reach further than the table you run them on.** A formula can read other tables in the
  same app with `GetRecord()` and `GetRecords()`, and can return the realm and the token owner's
  email. A table in another app, or one the token's role can't access, comes back empty. Don't run
  formula text from a model or another person, even on a read-only client. See the [warning in
  Formulas](formulas.md#running-a-formula).
- **They aren't a sandbox.** They apply to one client. If an agent can run code of its own, it can
  create another client with the same token, or call the API directly.

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
  still can. Call `count()` with the same condition first, and compare it with what you expect.

!!! note "`_curuser_` and relative dates"
    On a `user` or `multiuser` field, Quickbase reads `_curuser_` as the token's owner, ignoring case
    and surrounding spaces, so a value from someone else could match the owner's records. `F` raises
    `QueryError` for it there. On a text field it's literal text, so `F` allows it.

    On a date field, Quickbase reads text such as `today` or `1 day ago` as a date. That gives a
    model nothing it couldn't get by naming the date, but text it can't read matches nothing, with no
    error. Convert dates to `date` objects, so a bad one fails right away.

## Filters from a model

When a model needs to choose its own filters, have it send JSON, and don't let it write query strings.
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

Return a `QueryError` or `UnknownFieldError` to the model as the tool's result. Their messages say
where the mistake is and what's allowed, so it can correct itself, and they don't repeat the values
it sent. Nothing has been sent to Quickbase at that point.

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
  tells the model the list, so pass the same list to `where_from_dict()`, which enforces it.
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
  fields hold an email too. Decide whether a model or a log should see them.
- [Don't use a file's name as a path](files.md#downloading). Quickbase stores names as sent,
  including `../../etc/passwd`.

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

Rate limits and connection failures are retried for you. A write that timed out isn't, because it
may already have applied, so an agent should check the data before trying again.

| Exception | What it means for a write |
|---|---|
| [Mistakes caught before sending](errors.md#mistakes-caught-before-sending), such as `QueryError`, `InvalidValueError`, and `ReadOnlyError` | This request wasn't sent |
| `WriteError` | Some rows may have been written. If a large write stopped at a later batch, `result` covers only the earlier batches, `errors` can be empty, and the failed batch may have been written |
| `NetworkError`, `RequestTimeoutError`, `ServerError`, `InvalidResponseError` | Unknown, so check the data first |
| Other `APIError`s, such as `BadRequestError` | Quickbase answered with an error, so fix the cause rather than repeating the request |

`create_field()` with `required` or `unique` takes two requests, so if it raises an error, the field
[may already exist](schema.md). This includes `ReadOnlyError`, when only the first is allowed.

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
