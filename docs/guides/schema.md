# Changing the schema

These methods change your app's structure. To prevent them, see
[Limiting what a client can do](clients.md#limiting-what-a-client-can-do).

```python
app = qb.app("bq1234567")

projects = app.create_table("Projects", single_record_name="Project", plural_record_name="Projects")
projects.create_field("Name", "text")
projects.create_field("Status", "text-multiple-choice", choices=["Active", "Done"])
projects.create_field("Code", "text", unique=True)
```

`field_type` is Quickbase's own name for the type, such as `text`, `numeric`, `date`, `checkbox`, or
`currency`. For anything else, pass `properties`, which are sent as they are.

```python
projects.create_field("Score", "numeric", formula="1 + 1")
```

Quickbase sets `required` and `unique` in a second request, so if that one fails, the field exists
without them.

## Relationships

`create_relationship()` makes a table the child of another, and adds a foreign key field to the
child. `lookup_fields` are parent fields to show on the child.

```python
tasks = app.table("Tasks")
tasks.create_relationship(projects, foreign_key_label="Project", lookup_fields=["Name"])
```

`lookup_fields` are the parent's fields. `Relationship.lookup_field_ids` lists the lookups that
resulted, which are fields on the child, so don't pass one back in.

## Setup scripts you can run again

The `ensure_` methods return what's there and create only what's missing, so a script can stop partway
and be run again. They're also the way to add a lookup to an existing relationship, and to create a
summary field.

```python
app = qb.app("bq1234567")

projects = app.ensure_table("Projects")
projects.ensure_field("Name", "text", required=True)
projects.ensure_field("Code", "text", unique=True)

tasks = app.ensure_table("Tasks")
tasks.ensure_field("Hours", "numeric")

link = tasks.ensure_relationship(projects, foreign_key_label="Project")
tasks.ensure_lookup_field(link, "Name", label="Project Name")
tasks.ensure_summary_field(link, "Hours", "Hours Sum")
projects.ensure_field("Total Hours", "numeric", formula="Nz([Hours Sum])")
```

- `ensure_field()` finds a field by its label, ignoring case. It changes only what you passed that
  differs, including `formula`, and repairs `required` or `unique` if the second request failed before.
  It raises `SchemaMismatchError` rather than changing a field's type.
- `ensure_lookup_field()` finds a lookup by what it points at, so `label` can rename one that Quickbase
  named `Project - Name`.
- `ensure_summary_field()` is called on the child, and creates the field on the parent.
- **A summary with no child records is empty, not 0.** Code that expects a number should read a formula
  that uses `Nz()`, as above, rather than the summary.
- `ensure_table()` doesn't change a table that exists.

## Changing and deleting

```python
tasks.update_field("Notes", label="Comments", required=False)
tasks.delete_fields(["Old Field", "Older Field"])
tasks.drop(confirm="Tasks")
```

`delete_fields()` raises `WriteError` if any field couldn't be deleted, though the others will have
been. `drop()` deletes the table and all its records, so it requires the table's exact name.

These methods refresh the client's cached metadata for you. After changing the schema some other
way, call `qb.clear_schema_cache()`.
