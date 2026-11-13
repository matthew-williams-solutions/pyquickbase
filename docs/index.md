---
description: "What the SDK is, why to use it, and how to install it."
---

# pyquickbase

<p class="hero-lede">A typed Python SDK for the <a href="https://www.quickbase.com/">Quickbase</a> API,
with matching sync and async clients, and policies that limit what a client, or an AI agent, can do.</p>

[Get started](getting-started.md){ .md-button .md-button--primary }
[Try it in your browser](playground.md){ .md-button }

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

<div class="grid cards" markdown>

-   **Names, not IDs**

    Field labels and table names everywhere, with real `date` and `datetime` values.

-   **Queries that can't silently fail**

    `F("Status") == "Done"` instead of `{7.EX.'Done'}`, checked before it's sent.

-   **Errors that say what happened**

    A failed row raises `WriteError`, listing what was written and what wasn't.

-   **[Policies](policies/index.md)**

    Limit a client to the tables, fields, and records its job needs.

-   **[Built for AI agents](agents/index.md)**

    Typed operations for tool calls, results as plain data, and these docs as
    [llms.txt](llms.txt).

</div>

The Quickbase REST API accepts many mistakes without complaint. A lowercase operator acts like
"contains", an unknown field matches nothing, a failed row sits inside a successful response, and
writing `null` to a field does nothing. This SDK turns those into errors or correct behavior.

| With the REST API | With pyquickbase |
|---|---|
| Numeric field IDs everywhere | Field labels and table names, or IDs if you prefer |
| `{7.EX.'Done'}AND{9.BF.'today'}` | `(F("Status") == "Done") & (F("Due Date") < date.today())` |
| Broken queries return plausible results | Broken queries raise `InvalidQueryError` before sending |
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
    for failed in error.result.failed_rows:
        print("Failed:", rows[failed.row_index]["Title"], "-", *failed.messages)
```

```text
Saved: (25, 26)
Failed: Book venue - The field with ID '7' has an invalid choice.
```

[Try it in the playground](playground-app/index.html#an-import-that-loses-a-row), which uses simulated
data based on [recorded API behavior](api-notes.md#writes).

## Install

```bash
pip install pyquickbase==1.0.0rc13
```

It needs Python 3.11 or newer. Next, see [Getting started](getting-started.md).
