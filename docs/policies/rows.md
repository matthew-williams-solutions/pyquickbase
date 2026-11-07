# Limiting rows

`limit.rows(tables, where)` keeps a client to the records of those tables that match a condition:

| Request | What `limit.rows()` does |
|---|---|
| Queries, summaries, and deletes, including through `qb.request()` | ANDs the condition onto them |
| A filter on a field the condition uses | Allows it, unless a deny rule covers the field |
| A new summary or count field, from an `ensure_` method or a plan | Gets the limit in its filter |
| An existing summary whose filter doesn't hold the limit | Hidden |
| A lookup from a row-limited table | Hidden, and a new one is refused |
| A lookup onto a row-limited table | Allowed |
| Reports, file downloads, deleting file versions, and formulas | Refused |
| Inserts, upserts, and updates by `Record ID#` | Not checked |

It's defense in depth. Quickbase's [record-level rules](../api-notes.md#record-level-rules) for the
token's role are the real boundary, so set those as well.
