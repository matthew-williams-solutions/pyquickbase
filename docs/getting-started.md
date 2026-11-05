# Getting started

## Before you start

You need three things from Quickbase:

- **Your realm**, the `acme` in `acme.quickbase.com`.
- **A user token** with access to your app. Quickbase explains how to create one in its
  [authentication docs](https://developer.quickbase.com/auth). Treat it like a password, and give it
  access to as few apps as possible.
- **Your app's ID**, which appears in the app's URL and usually starts with `b`.

## Install

```bash
pip install pyquickbase==1.0.0rc8
```

## Read some records

Both clients have the same API. Use the sync client for scripts and notebooks, and the async client
in applications that already use asyncio or Trio.

=== "Sync"

    ```python
    from pyquickbase import F, Quickbase

    with Quickbase(realm="acme", token="your_user_token") as qb:
        tasks = qb.app("bq1234567").table("Tasks")

        for task in tasks.query(where=F("Status") == "Done", select=["Title"]):
            print(task.id, task["Title"])
    ```

=== "Async"

    ```python
    from pyquickbase import AsyncQuickbase, F

    async with AsyncQuickbase(realm="acme", token="your_user_token") as qb:
        tasks = qb.app("bq1234567").table("Tasks")

        async for task in tasks.query(where=F("Status") == "Done", select=["Title"]):
            print(task.id, task["Title"])
    ```

To keep the token out of your code, set `QB_REALM` and `QB_USER_TOKEN` in the environment and use
`Quickbase.from_env()`.

```python
from pyquickbase import Quickbase

with Quickbase.from_env() as qb:
    print(qb.app("bq1234567").info().name)
```

## Write a record

```python
tasks.insert([{"Title": "Pour slab", "Status": "Not Started"}])
```

## Next steps

- [Reading records](guides/reading.md) covers queries, paging, and record values.
- [Writing records](guides/writing.md) covers inserts, updates, upserts, and deletes.
- [Handling errors](guides/errors.md) lists what can go wrong.
