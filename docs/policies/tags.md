# Tags and new fields

## Tags, and what derived fields read

A tag names a group of fields, such as everything about pay, for `fields.tagged()`. Each entry is
`"Table.Field"`, `("Table", "Field")` when a name holds a dot, `fields.all()`, or
`fields.labeled_like(...)`, which matches in every table.

**A formula, lookup, or summary is in every tag of the fields it reads,** through any chain of
them, including the fields a summary's filter uses. So `deny.all(fields.tagged("pay"))` hides a
formula of `Hourly Rate`, a lookup of that formula on another table, and a summary of the lookup. A
formula that can't be read counts as reading every field of its table, and one that calls a function
that reads other records counts as reading every field of the app.

```mermaid
flowchart TD
    subgraph Staff
        direction LR
        H["Hourly Rate<br/>tag: pay"] --> D["Day Rate<br/>formula"]
    end
    subgraph Shifts
        L["Staff - Day Rate<br/>lookup"]
    end
    subgraph Projects
        T["Total Cost<br/>summary"]
    end
    D --> L --> T
    classDef pay fill:#e5393526,stroke:#e53935
    tag H,D,L,T pay
```

Every field in the chain is in `pay`, so `deny.all(fields.tagged("pay"))` hides all four, on all
three tables.

**A new formula, lookup, or summary may only read what the client may read,** as anyone who can read
it sees what it shows. The SDK's methods check this before sending one, and `qb.request()` can't make
one.

## Fields added after binding

The bound policy holds every table and field by ID, as Quickbase never reuses a field's ID within a
table, so a rule can't come to mean another field. A renamed field keeps its rules until you bind
again. A field added after binding gets `new_fields`:

| `new_fields` | A new field is |
|---|---|
| `"read"`, the default | Read, filtered, and sorted wherever a rule reads `fields.all()` or `fields.new()` |
| `"hidden"` | Hidden |
| A tag's name | In that tag, so the rules for the tag apply |

**A new field is never writable** until the policy is bound again, whatever the rules say.
