# Changing the schema

This page covers creating and changing tables, fields, and relationships. Every method here is also
on the async client, where you `await` it. To stop a client from changing the schema, see
[Limiting what a client can do](clients.md#limiting-what-a-client-can-do).

## Creating a table and its fields

`create_table()` returns the new table, and `create_field()` takes a label and a
[field type](../field-types.md).

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    app = qb.app("bq1234567")

    projects = app.create_table(
        "Projects", single_record_name="Project", plural_record_name="Projects"
    )
    projects.create_field("Name", "text")
    projects.create_field("Status", "text-multiple-choice", choices=["Active", "Done"])
    projects.create_field("Budget", "currency", properties={"decimalPlaces": 2})
    projects.create_field("Score", "numeric", formula="1 + 1")
```

The other examples on this page use `qb`, a client made in the same way.

- `choices` are the options of a `text-multiple-choice` or `multitext` field. Quickbase refuses them on
  other types. **They don't restrict what can be written** unless you also pass
  `properties={"allowNewChoices": False}`, because a new field accepts new choices by default.
- `properties` are any other settings, sent as they are. The
  [field types page](../field-types.md#field-settings) lists common ones.
- `formula` makes it a formula field. See [Formulas](formulas.md#formula-fields).

To run the same script more than once, use the `ensure_` methods in
[Setup scripts you can run again](#setup-scripts-you-can-run-again).

## Making a field required or unique

Quickbase sets `required` and `unique` in a second request, after creating the field. If that request
fails, the field exists without them, so run `ensure_field()` to repair it once the problem is fixed.

```python
tasks = qb.app("bq1234567").table("Tasks")

tasks.create_field("Serial", "text", unique=True)
tasks.update_field("Title", required=True)
```

What's already in the table decides whether Quickbase allows this:

| Setting | When Quickbase refuses it |
|---|---|
| `required=True` | Any record has this field empty. **That includes every new field on a table with records**, so create the field, fill it in, and then make it required |
| `unique=True` | The field already holds duplicate values. A new, empty field is allowed |

Inserting a record without a required field's value then raises `WriteError`.

## Linking tables with a relationship

Call `create_relationship()` on the child table and pass the parent. This example makes each task
belong to a project.

```python
app = qb.app("bq1234567")
projects = app.table("Projects")
tasks = app.table("Tasks")

link = tasks.create_relationship(projects, foreign_key_label="Project", lookup_fields=["Name"])
```

| Field | Table | What it is |
|---|---|---|
| `Project` | Child | The foreign key, a `numeric` field holding the parent's record ID |
| `Project - Name` | Child | A lookup for each of `lookup_fields`, named after the foreign key and the field |
| `Task records` | Parent | A list of the child's records, named after the child's record name |
| `Add Task` | Parent | A link to add a child record |

- `lookup_fields` are the parent's fields. `link.lookup_field_ids` lists the lookups that resulted,
  which are on the child, so don't pass one back in.
- `tasks.relationships()` lists a relationship. `projects.relationships()` doesn't, because Quickbase
  only lists one on its child.
- Calling `create_relationship()` again makes another relationship, even with the same label.
  `ensure_relationship()` finds the existing one.

## Adding lookups and summaries

Use these two methods to add to a relationship, including one that already exists. Call both on the
child table.

- `ensure_lookup_field()` shows a parent's field on the child.
- `ensure_summary_field()` calculates a value from the child's records, and puts it on the parent.

```python
app = qb.app("bq1234567")
projects = app.table("Projects")
tasks = app.table("Tasks")

link = tasks.ensure_relationship(projects, foreign_key_label="Project")
tasks.ensure_lookup_field(link, "Name", label="Project Name")

tasks.ensure_field("Hours", "numeric")
tasks.ensure_summary_field(link, "Hours", "Total Hours")
tasks.ensure_summary_field(link, "Hours", "Longest Task", function="MAX")
projects.ensure_field("Hours Or Zero", "numeric", formula="Nz([Total Hours])")
```

`function` defaults to `SUM`, and must be in capitals:

| `function` | Worked on | The summary's type |
|---|---|---|
| `SUM` | `numeric`, `currency` | The same as the child's field |
| `AVG`, `STD-DEV` | `numeric` | `numeric` |
| `MAX`, `MIN` | `numeric`, `date` | The same as the child's field |
| `DISTINCT-COUNT` | `numeric`, `text` | `numeric` |
| `COMBINED-TEXT` | `text` | `multitext` |
| `COMBINED-USER` | `user` | `multiuser` |

We tested these combinations, and others may work. Quickbase refused `SUM` of a `checkbox`, `MAX` of
`text`, and `COUNT` of every field we tried it on, with `BadRequestError`.

**A summary with no child records is empty, not 0.** Code that expects a number should read a formula
that uses `Nz()`, such as `Hours Or Zero` in the example here, rather than the summary.

## Setup scripts you can run again

The `ensure_` methods return what's there and create only what's missing, so a script can stop partway
and be run again.

```python
app = qb.app("bq1234567")

projects = app.ensure_table("Projects")
projects.ensure_field("Name", "text")
projects.ensure_field("Code", "text", unique=True)

tasks = app.ensure_table("Tasks")
tasks.ensure_field("Hours", "numeric")

link = tasks.ensure_relationship(projects, foreign_key_label="Project")
tasks.ensure_lookup_field(link, "Name", label="Project Name")
tasks.ensure_summary_field(link, "Hours", "Hours Sum")
projects.ensure_field("Total Hours", "numeric", formula="Nz([Hours Sum])")
```

| Method | Finds what's there by | If it exists |
|---|---|---|
| `ensure_table()` | Its name, ignoring case | Returns it unchanged |
| `ensure_field()` | Its label, ignoring case | Changes only what you passed that differs, including `formula`. Raises `SchemaMismatchError` rather than changing its type |
| `ensure_relationship()` | The parent, and the foreign key's label ignoring case | Returns it unchanged |
| `ensure_lookup_field()` | The parent's field it points at, so a renamed lookup is still found | Applies `label` if you passed one |
| `ensure_summary_field()` | Its label on the parent, ignoring case | Returns it. Raises `SchemaMismatchError` if that field isn't this same summary |

`required=True` in `ensure_field()` follows
[the same rules as anywhere else](#making-a-field-required-or-unique), so a script that adds a required
field to a table with records needs to fill it in first.

## Changing a field

`update_field()` changes only what you pass, and takes a label or an ID. If Quickbase refuses, the
field stays as it was.

```python
tasks = qb.app("bq1234567").table("Tasks")

tasks.update_field("Notes", label="Comments")
tasks.update_field("Status", properties={"choices": ["Open", "Done", "Blocked"]})
```

- `choices` in `properties` replaces the whole list, so include the ones you want to keep.
- `formula` only works on a formula field, and can't be blank, because Quickbase would accept that and
  empty the field's values.
- It can't change a field's type. Create a new field instead.

## Deleting fields and tables

```python
tasks = qb.app("bq1234567").table("Tasks")

tasks.delete_fields(["Old Field", "Older Field"])
tasks.drop(confirm="Tasks")
```

`delete_fields()` deletes the fields' data too. It raises `WriteError` if any field couldn't be
deleted, such as a built-in field, **though the others in the list will have been**. If one doesn't
exist, it raises `UnknownFieldError` and deletes nothing.

`drop()` deletes the table and all its records, so it requires the table's exact name.

## When something is refused

| What happened | Exception | What's left behind |
|---|---|---|
| Unknown field type, a label already in use, or `choices` on the wrong type | `BadRequestError` | Nothing was created |
| An unknown key in `properties` | `BadRequestError` | Nothing was created or changed |
| `required` or `unique` refused while creating a field | `BadRequestError` | **The field exists without them** |
| `required` or `unique` refused while updating a field | `BadRequestError` | The field is unchanged |
| `create_table()` with a name that's in use | `BadRequestError` | Nothing was created |
| `ensure_field()` with a different type | `SchemaMismatchError` | The field is unchanged |
| `ensure_summary_field()` with a label that's in use | `SchemaMismatchError` | The field is unchanged |
| A field label or ID that doesn't exist | `UnknownFieldError` | Nothing was changed |
| `update_field()` with nothing to change, `delete_fields([])`, or the wrong `confirm` | `InvalidValueError` | Nothing was sent |
| `delete_fields()` with a field that can't be deleted | `WriteError` | The other fields were deleted |

See [Handling errors](errors.md) for how these exceptions relate.

## The metadata cache

These methods refresh the client's cached metadata for you. After changing the schema some other way,
such as in the Quickbase interface, call `qb.clear_schema_cache()`.
