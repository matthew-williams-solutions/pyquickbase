# Find open tasks
from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    for task in tasks.query(where=F("Status") == "Open", select=["Title", "Due Date"]):
        print(task.id, task["Title"], task["Due Date"])
