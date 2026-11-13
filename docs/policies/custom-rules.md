---
description: "Refusing requests with logic of your own."
---

# Rules of your own

For logic the rules can't express, add an object with a `decide(request)` method to `rules`. It sees
each request the built-in rules allow, as a `ReadRequest` or `WriteRequest`, which holds no values,
so it's safe to log. It returns None to leave the request alone, or a `PolicyDecision` with
`allowed=False` to refuse it. **It can only refuse,** so a bug in it can't allow more than the rules
do.

```python
from pyquickbase import PolicyDecision, Policy, WriteRequest, allow, fields


class SmallBatches:
    def decide(self, request):
        if isinstance(request, WriteRequest) and (request.row_count or 0) > 100:
            return PolicyDecision(allowed=False, verb="insert", reason="Up to 100 rows at a time")
        return None


policy = Policy(rules=[allow.insert("Job Tasks", fields.all()), SmallBatches()])
```

`offers()` asks about one field and verb at a time, so a rule that decides each field on its own is
offered exactly what it allows. It runs once per request, so a write split into batches is checked
batch by batch, and it sees each batch's own `row_count`. A custom rule can't be saved with
`to_json()`, so pass it to `from_json(rules=[...])` again.

A `WriteRequest` says what a write would do:

| Attribute | What it holds |
|---|---|
| `action` | `insert`, `update`, `upsert`, `delete`, `delete_file`, a schema change such as `create_field` or `delete_relationship`, or `other` |
| `table_id` | The table's ID, or for a relationship, the child's |
| `field_ids` | The IDs of the fields it sets, or for `update_field` and `delete_field`, the fields it changes |
| `row_count` | How many rows are in this request |
| `app_id` | For `create_table` and `update_table`, the app |
| `parent_table_id` | For a relationship, the parent table |
| `field_type` | For `create_field`, the new field's type |
| `derived` | The formula, lookup, and summary fields it makes or changes |
| `method`, `path` | The HTTP request, for anything `action` doesn't cover |

A `ReadRequest` says what a read would do:

| Attribute | What it holds |
|---|---|
| `action` | `query`, `summarize`, `delete`, `report`, `summary_report`, `file`, `formula`, `tables`, `fields`, `relationships`, `reports`, `app`, `roles`, or `other` |
| `table_id` | The table's ID |
| `field_ids`, `filter_field_ids`, `sort_field_ids` | The fields it returns, filters on, and sorts by |
| `limit`, `skip` | How many records it asks for, which Quickbase calls `top`, and how many it passes over |
| `report_id` | The report it runs |
| `method`, `path` | The HTTP request, for anything `action` doesn't cover |

**Treat `None` as "couldn't be determined", not as "nothing".** For example, `field_ids` is `None`
if a row writes a field ID in an unusual form, such as `07`, which Quickbase reads as field 7, and a
query without `sortBy` has `None` for `sort_field_ids`, because the table's default sort would apply.
Have your rule ignore an `action` it doesn't recognize, as a later version can add one.

`upsert` means a write that may do either, which isn't only `table.upsert()`. Any merge on a field
other than `Record ID#` is one, because Quickbase creates a record when the value doesn't match. So is
a write without a merge field that goes through `qb.request()` or to a table with a different key
field, because it can change an existing record.
