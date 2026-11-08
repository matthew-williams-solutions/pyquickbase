# Writing rules

A policy is a list of rules over tables and fields, by name and label. Bind it to the app once,
which looks up every name and reports every problem at once, then give the bound policy to the
client.

```python
from pyquickbase import F, Policy, Quickbase, allow, deny, fields, limit, tables

policy = Policy(
    classes={
        "pay": ["Tasks.Amount"],
        "internal": [("Tasks", "Code"), fields.labeled_like("Old*")],
    },
    rules=[
        allow.read(tables.all(), max_paging_depth=50),
        allow.insert("Tasks", fields.all()),
        allow.update("Tasks", ["Status", "Due Date", "Notes"]),
        allow.delete("Tasks", max_records_per_delete=3),
        limit.rows("Tasks", F("Project") == "Roof"),
        deny.all(fields.classed("pay", "internal")),
    ],
)

with Quickbase(realm="acme", token="your_user_token") as setup:
    bound = policy.bind(setup.app("bq1234567"))

with Quickbase(realm="acme", token="your_user_token", policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    tasks.update([{"Record ID#": 1, "Status": "Done"}])  # Allowed
    # tasks.update([{"Record ID#": 1, "Amount": 90}])  # Raises PolicyRefusedError
```

With the async client, it's `await policy.bind(app)`. Bind with a client that has no policy, so it
can see every table and field.

**Anything no rule allows is refused, and a deny always wins.** Each rule names its tables first:
a table's name or ID, a list of them, `tables.all()` for every table in the app and any the client
creates, or `tables.created()` for only those the client creates. Most then name fields: labels or
IDs, `fields.all()`, `fields.new()` for fields added after binding, `fields.classed(...)`, or
`fields.labeled_like(...)`, which matches labels such as `_*`, ignoring case.

| Rule | What it allows |
|---|---|
| `allow.read(tables, fields, ...)` | Reading the fields, and filtering and sorting by them, unless it gives its own `filter=` or `sort=`. It also takes `max_paging_depth`, `max_records_scanned`, and `user_emails` |
| `allow.filter(...)`, `allow.sort(...)` | Filtering or sorting by fields without reading them |
| `allow.insert(...)`, `allow.update(...)` | Setting the fields in new or existing records. An upsert needs both |
| `allow.delete(tables, max_records_per_delete=None)` | Deleting records. With `max_records_per_delete`, the client counts what matches first |
| `allow.download(...)`, `allow.delete_file(...)` | Downloading files, or deleting their versions |
| `allow.report(table, reports)` | Running these reports, by name or ID, or every report with `None` |
| `allow.schema(tables, *verbs, ...)` | Changing the schema, as [below](schema.md) |
| `allow.create_tables()` | Creating tables in the bound app |
| `deny.all(selector)`, `deny.read(...)`, `deny.update(...)`, and the rest | Refusing what they name, whatever allows it |
| `limit.rows(tables, where)` | Reaching only the records that match, as [below](rows.md) |

Things to know:

- **Writing a field doesn't need read access to it,** so a client can set a field it can't see.
  Such a field can still be named, but its values aren't returned.
- **`Record ID#` is readable wherever anything is,** because the SDK uses it to page through records.
- **`max_paging_depth` limits each query, not the client.** A query needs a `limit` of at most this, and
  it's refused rather than cut short. Where several rules read a table, the highest applies. It
  defaults to 100, and `max_records_scanned` to 10,000. A summary that only counts reads no records,
  so `max_records_scanned` doesn't limit it.
- **A field you may filter or sort by reveals something about its values,** even if it isn't
  returned, because a model can see which records match and in which order.
- **A report's own filter and sort can use any field,** and someone can change them in the Quickbase
  interface, so list only reports you've checked. A summary report's columns can't be matched to
  fields, so allowing one shows all of them.
- **Some things are refused unless the policy is `Policy.read_only()`:** running a formula, which can
  read any table, an app's details and roles, and any request it doesn't recognize. Use a separate
  client for those.
- **A delete needs `filter` on the fields its filter uses,** because the number deleted shows how
  many records matched.
- **`insert` alone can't be limited to creating records on a table with a custom key field.** If
  someone has changed a table's key field in the Quickbase UI, a row with an existing value in that
  field [changes that record](../api-notes.md#writes). `table.insert()` refuses those tables, so use
  `upsert()`, which needs both `insert` and `update`. `qb.request()` can't look up a table's key
  field, so any write through it without a merge field needs both.
