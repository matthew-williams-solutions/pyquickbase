# pyquickbase

An unofficial, typed Python SDK for the [Quickbase](https://www.quickbase.com/) API, with matching
sync and async clients.

!!! warning "Release candidate"
    This is a release candidate, so small changes are still possible before 1.0. Pin the exact version
    you install. It isn't affiliated with or endorsed by Quickbase, Inc.

[Try it in your browser](playground.md), with nothing to install.

```python
from datetime import date
from pyquickbase import F, Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    overdue = (F("Status") != "Done") & (F("Due Date") < date.today())
    for task in tasks.query(where=overdue, select=["Title", "Due Date"], order_by="-Due Date"):
        print(task.id, task["Title"], task["Due Date"])

    tasks.update([{"Record ID#": 7, "Status": "Done"}])
```

## Why use it

The Quickbase REST API accepts many mistakes without complaint. A lowercase operator acts like
"contains", an unknown field matches nothing, a failed row sits inside a successful response, and
writing `null` to a field does nothing. This SDK turns those into errors or correct behavior.

| With the REST API | With pyquickbase |
|---|---|
| Numeric field IDs everywhere | Field labels and table names, or IDs if you prefer |
| `{7.EX.'Done'}AND{9.BF.'today'}` | `(F("Status") == "Done") & (F("Due Date") < date.today())` |
| Broken queries return plausible results | Broken queries raise `QueryError` before sending |
| Manual `skip` and `top` paging | `for record in table.query(...)` |
| Rows like `{"7": {"value": "Done"}}` | `record["Status"]`, with real `date` and `datetime` values |
| Failed rows inside a successful response | `WriteError`, listing what failed and what was written |

## See the difference

Say a page shows visitors the tasks for their project, using the project from its URL. Building that
query by hand looks harmless, until someone edits the URL:

```python
from pyquickbase import F, QueryError, Quickbase

# A page shows visitors their project's tasks, using the project from its URL.
# This visitor edited the URL.
project = "1'}OR{3.GT.'0"

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    print(f"Project 1 has {tasks.count(F('Related Project') == 1)} tasks")

    body = {"from": "bqtasks", "where": "{12.EX.'" + project + "'}"}
    records = qb.request("POST", "/records/query", json=body)["data"]
    print(f"Raw API: no error, and it returned {len(records)} tasks")

    try:
        tasks.count(F("Related Project") == project)
    except QueryError as error:
        print(f"pyquickbase: QueryError. {error}")
```

```text
Project 1 has 4 tasks
Raw API: no error, and it returned 12 tasks
pyquickbase: QueryError. Quickbase can't search for values containing { or }.
```

The visitor's text rewrote the raw query, so it returned the whole table, with no error. `F` treats
text as a value and refuses this one before anything is sent. Hand-written query strings can't get this protection,
so [use `F` for text you don't control](guides/reading.md#writing-queries).

[Run this in the playground](playground-app/index.html#block-query-injection), which uses simulated
data based on [recorded API behavior](api-notes.md#query-pitfalls).

## Install

```bash
pip install pyquickbase==1.0.0rc1
```

It needs Python 3.11 or newer. Next, see [Getting started](getting-started.md).
