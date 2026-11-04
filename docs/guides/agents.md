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
- **Formulas reach further than the table you run them on.** A formula can read other tables with
  `GetRecord()` and `GetRecords()`, and can return the realm and the token owner's email. Don't run
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
held to its limits as well. Record-level rules haven't been tested, and neither has whether a
restricted user's formula can read a table their role has no access to.

## Untrusted values

Use [`F`](reading.md#writing-queries) for anything a model or user supplied, and never build a
`where` string from it. The SDK can't tell an added condition from one you meant to write, so a
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

!!! warning "`_curuser_` and other relative values are unconfirmed"
    `F` sends `_curuser_` and `today` unchanged. Quickbase treats both as literal text on a text
    field, but what they do on other fields hasn't been tested. A value from someone else might
    match the token owner's records on a user field, and relative values on a date field may behave
    the same way. Until that's known, don't pass a model's text to those fields. Convert dates to
    `date` objects, and check user values against the ones you expect.

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
