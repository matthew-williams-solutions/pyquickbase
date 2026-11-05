# Generated classes

`pyquickbase codegen` reads your tables' schemas and writes a Python module with a class for each one.
Your editor and type checker then know every field's name and type, so they catch mistakes before the
code runs.

## Generating the module

The command reads `QB_REALM` and `QB_USER_TOKEN` from the environment, and only ever reads from
Quickbase. `--token-env` names a different variable for the token.

```bash
pyquickbase codegen --app bq1234567 --output src/myapp/tables.py
pyquickbase codegen --app bq1234567 --output src/myapp/tables.py --table Tasks --table Projects
```

The links Quickbase makes for each relationship, such as `Add Task` and `Task records`, are left out,
because only people use them and every query would fetch them. `--include-links` keeps them.

It won't replace a file it didn't generate. The module holds table IDs and field labels, but no realm or
token, so check that you're comfortable with those in your repository before committing it.

## Reading

Pass a generated class to `app.typed()`. A class is named after its table, and an attribute after its
field's label, so the `Tasks` table with a `Due Date` field gives you `Tasks` and `due_date`.

```python
from pyquickbase import Quickbase

from myapp.tables import Tasks

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").typed(Tasks)

    for task in tasks.query(
        where=Tasks.fields.status == "Open",
        order_by=[Tasks.fields.due_date.desc, Tasks.fields.title],
        limit=50,
    ):
        print(task.id, task.title, task.due_date)

    print(tasks.count(Tasks.fields.status == "Open"))
```

`Tasks.fields` works like `F`, by field ID. `order_by` takes these fields or their IDs, and raises
`QueryError` for a label such as `"Title"`, because the class uses IDs so that renaming a label can't
break it. Each record is a frozen dataclass, and a record's `repr` leaves out its values, because they may
be sensitive.

| Field type | Attribute type |
|---|---|
| Text types, `email`, `url`, `phone`, `address`, and `timeofday` | `str`, which is `""` when empty |
| `currency` | `Decimal \| None` |
| Foreign keys, and `numeric` fields set to 0 decimal places | `int \| None` |
| Other `numeric` fields, `rating`, `percent`, and `duration` (in milliseconds) | `float \| None` |
| `date` | `date \| None` |
| `timestamp` | `datetime \| None` |
| `checkbox` | `bool`, which is false when empty |
| `multitext` | `list[str]` |
| `user` | `User \| None`, which you can also write |
| `multiuser` | `list[User]`, which you can also write |
| `file` | `FileAttachment \| None`, as described in [File attachments](files.md) |
| `vCardButton` and `ICalendarButton` | `str`, a relative link, and read-only |
| `predecessor` | `str`, the record IDs it links to, such as `1;2`. Write it as one ID or several separated by commas |
| Anything else | `Any` |

An ordinary `Record` holds every number as a `float`. Generated classes convert them, so money reads
exactly. **Quickbase uses decimal places only for display**, and stores `1.5` in a field set to 0 places.
A value like that raises `InvalidResponseError`, without showing the value, rather than being rounded.

### Reading only some fields

`query()`, `query_page()`, `first()`, and `get()` read every field of the class unless you pass `select`,
which takes fields of the class, alone or in a list. Record ID# is always read. The other attributes are
left unset, so reading one raises `AttributeError`, saying it wasn't selected, rather than holding a value
that isn't true.

```python
for task in tasks.query(select=[Tasks.fields.title, Tasks.fields.due_date]):
    print(task.id, task.title, task.due_date)
    task.status  # AttributeError: Tasks.status wasn't selected in the query that read this record
```

Comparing two such records with `==` raises `AttributeError` too, and `tool_result()` leaves the unset
attributes out.

## Reading a page at a time

Use `query_page()` when you control the paging yourself, such as for a list endpoint. It takes `where`,
`order_by`, `top`, and `skip`, and returns a `TypedPage` whose `records` are instances of the class, so
money still reads as `Decimal`.

```python
from myapp.tables import Tasks

tasks = qb.app("bq1234567").typed(Tasks)

page = tasks.query_page(where=Tasks.fields.status == "Open", order_by=Tasks.fields.due_date, top=25)
print([task.title for task in page.records])
print(page.total, page.skip, page.has_more, page.next_skip)

if page.has_more:
    following = tasks.query_page(
        where=Tasks.fields.status == "Open",
        order_by=Tasks.fields.due_date,
        top=25,
        skip=page.next_skip,
    )
```

`total` counts every match, not just this page. Like other typed methods, it checks the class against
the table on first use. Paging uses offsets, so records deleted between pages can cause others to be
skipped without any error.

## Writing

`Tasks.new()` builds a row for a new record, and `Tasks.change()` builds one for an existing record.
They only accept fields you can write, so setting a formula, lookup, summary, or built-in field is a
type error, and raises `TypeError` when the code runs. A type checker also refuses a row made for
another class.

```python
tasks.insert([Tasks.new(title="Pour slab", due_date=date(2027, 1, 15))])
tasks.update([Tasks.change(7, status="Done", notes=None)])
tasks.upsert([Tasks.new(code="A-1", title="Pour slab")], merge_on=Tasks.fields.code)
tasks.delete(Tasks.fields.status == "Cancelled")
```

Leaving an argument out leaves that field alone, and `None` clears it, except for file fields, which
take a `FileUpload`. A row made by a class for another table raises `InvalidValueError`, because its field
IDs would mean different fields here. `insert()`, `update()`, and `upsert()` also take `batch_size`.

`tasks.table` is the ordinary untyped table, for anything else, such as reports and file downloads.
Its `where`, `select`, and `order_by` also accept `Tasks.fields`, but its records aren't converted, so
money reads as a `float`.

The other examples on this page use `tasks`, a typed table made in the same way. Every method is also on
the async client, where you `await` it, or use `async for` on `query()`.

## Keeping it up to date

A generated class is compared with its table the first time it's used, and `SchemaMismatchError` lists
what's different if a field was deleted, changed type, or became read-only. Reads and writes stop rather
than risk returning wrong types. Run the command again to fix it. Adding a field, renaming one, or a
field becoming writable doesn't cause an error, because the class uses field IDs and is still safe.

That first check uses cached metadata, 5 minutes old at most by default (see `schema_ttl`), and later
changes aren't noticed. A long-running process can call `tasks.check_schema()` from time to time, which
always fetches the fields again.

**Restricted users.** Quickbase leaves out fields a user's role can't see, so a hidden field looks the
same as a deleted one, and the class raises `SchemaMismatchError` for that user. Generate a class with a
token that has the same access as the code using it. A class generated by the most restricted user also
works for anyone who can see more.

To catch changes sooner, run this in CI. It changes nothing, and fails if the file is out of date.

```bash
pyquickbase codegen --app bq1234567 --output src/myapp/tables.py --check
```

## How names are chosen

Names use ASCII letters, digits, and underscores, and labels are lowercased with other characters
turned into underscores, so a label can't inject code into the module.

| Label | Attribute |
|---|---|
| `Due Date` | `due_date` |
| `1st` | `f_1st` |
| `class`, `str`, `new` | `class_`, `str_`, `new_` |
| `日本語`, `😀` | `field_6`, using the field's ID |

If two labels give the same name, such as `Due Date` and `Due-Date`, the field with the lowest ID keeps
it and the other gets its ID added: `due_date_9`. A new field therefore never takes an existing field's
name. Record ID# is always `id`. Renaming a label renames its attribute the next time you generate.

**Names can move.** If the field holding a plain name is deleted, the next field with that name takes it
when you generate again, so `due_date` can come to mean a different field with no error. Class names work
the same way, and a clash between them is settled only among the tables you generate, so `--table` can
change a class's name. Review the diff when you generate again, and use `--check` in CI.

## When something is refused

| What happened | Exception | What's left behind |
|---|---|---|
| The class no longer matches its table, or a field is hidden from this token | `SchemaMismatchError` | Nothing was sent |
| `select` or `order_by` with a label or a value that isn't a field of the class | `QueryError` | Nothing was sent |
| Reading an attribute that `select` left out | `AttributeError` | Nothing was sent |
| A row made by a class for another table | `InvalidValueError` | Nothing was written |
| Setting a field that can't be written, through `new()` or `change()` | `TypeError` | The row wasn't made |
| A record holding a fraction in a field generated as `int` | `InvalidResponseError` | Nothing was changed |
| A row Quickbase refuses | `WriteError` | The other rows were written |
| `codegen` with an output file it didn't generate | The command fails | The file is unchanged |
| `codegen --check` with a file that's out of date | The command fails | The file is unchanged |
