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
projects.create_field("Score", "numeric", properties={"formula": "1 + 1"})
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
