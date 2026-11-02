# pyquickbase

An unofficial, typed Python SDK for the [Quickbase](https://www.quickbase.com/) API, with matching
sync and async clients.

!!! warning "Alpha"
    This project is in early alpha, so anything may change before 1.0. Pin the exact version you
    install. It isn't affiliated with or endorsed by Quickbase, Inc.

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

## Install

```bash
pip install pyquickbase==0.1.0a1
```

It needs Python 3.11 or newer. Next, see [Getting started](getting-started.md).
