# Recipes

Complete policies for common jobs. Each one starts from nothing allowed and adds only what the job
needs. Bind each with `policy.bind(app)` before giving it to a client, except the first, which needs
no binding.

## A dashboard or report

Reads everything the token can, and changes nothing.

```python
from pyquickbase import Policy

policy = Policy.read_only()
```

## A support agent

Reads tasks without costs, a page at a time, and changes only their status and notes.

```python
from pyquickbase import Policy, allow, deny, fields

policy = Policy(
    tags={"internal": ["Tasks.Amount", "Tasks.Code"]},
    rules=[
        allow.read("Tasks", max_paging_depth=50),
        allow.update("Tasks", ["Status", "Notes"]),
        deny.all(fields.tagged("internal")),
    ],
)
```

## A data-entry bot

Adds tasks and can't read, change, or delete anything. Writing a field doesn't need read access to
it.

```python
from pyquickbase import Policy, allow

policy = Policy(rules=[allow.insert("Tasks", ["Title", "Status", "Due Date", "Project"])])
```

## One project's assistant

Reads and updates only one project's tasks, and deletes at most three records at a time.

```python
from pyquickbase import F, Policy, allow, limit

policy = Policy(
    rules=[
        allow.read("Tasks", max_paging_depth=100),
        allow.update("Tasks", ["Status", "Notes", "Due Date"]),
        allow.delete("Tasks", max_records_per_delete=3),
        limit.rows("Tasks", F("Project") == "Roof"),
    ]
)
```

`limit.rows()` doesn't check an update by `Record ID#`, so pair it with a role whose
[record-level rules](../api-notes.md#record-level-rules) do.

## A schema assistant

Adds and changes fields on one table, but can't delete fields or tables, or make formulas.

```python
from pyquickbase import Policy, allow

policy = Policy(
    rules=[
        allow.read("Tasks"),
        allow.schema("Tasks", "create_field", "update_field"),
    ]
)
```

See [Schema changes](schema.md) for every verb, and which table each one is granted on.

## A code page builder

Writes an app's code pages for an agent. Nothing else in the app is open to it, but a page runs with the
permissions of whoever opens it, as [Code pages](rules.md#code-pages) explains.

```python
from pyquickbase import Policy, allow

policy = Policy(rules=[allow.code_pages(write=True)])
```

## A nightly sync

Upserts records by a unique code, in batches of up to 500 rows, with a rule of your own.

```python
from pyquickbase import PolicyDecision, Policy, WriteRequest, allow, fields


class Batches:
    def decide(self, request):
        if isinstance(request, WriteRequest) and (request.row_count or 0) > 500:
            return PolicyDecision(allowed=False, verb="insert", reason="Up to 500 rows at a time")
        return None


policy = Policy(
    rules=[
        allow.read("Tasks", ["Code"]),
        allow.insert("Tasks", fields.all()),
        allow.update("Tasks", fields.all()),
        Batches(),
    ]
)
```

An upsert needs both `insert` and `update`. See [Rules of your own](custom-rules.md).
