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

You import three rows, and one has an invalid status:

```python
rows = [
    {"Title": "Order chairs", "Status": "Open"},
    {"Title": "Book venue", "Status": "Finished"},  # Not a valid status
    {"Title": "Send invites", "Status": "Open"},
]
```

The REST API saves the other two and returns HTTP 207. That's a success status, so a normal check
passes, and the failed row is only listed inside the response body:

```python
response = httpx.post(url, headers=headers, json=body)
response.raise_for_status()  # Passes, because 207 is a success status
print("Import finished")  # But one row wasn't saved
```

pyquickbase checks the response for you and raises an error that says what happened:

```python
try:
    tasks.insert(rows)
except WriteError as error:
    print("Saved:", error.result.created_ids)
    for failed in error.result.errors:
        print("Failed:", rows[failed.index]["Title"], "-", *failed.messages)
```

```text
Saved: (13, 14)
Failed: Book venue - The field with ID '7' has an invalid choice.
```

[Try it in the playground](playground-app/index.html#an-import-that-loses-a-row), which uses simulated
data based on [recorded API behavior](api-notes.md#writes).

## Install

```bash
pip install pyquickbase==1.0.0rc2
```

It needs Python 3.11 or newer. Next, see [Getting started](getting-started.md).
