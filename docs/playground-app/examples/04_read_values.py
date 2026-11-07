# Read values as Python types
from pyquickbase import Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    task = next(
        iter(tasks.query(select=["Title", "Due Date", "Hours", "Date Created"], max_total=1))
    )

    print(task.id, task["Title"])
    print(repr(task["Due Date"]))
    print(repr(task["Date Created"]))
    print(repr(task["Hours"]))
    print(dict(task))
