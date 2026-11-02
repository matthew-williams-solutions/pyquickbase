# Combine conditions and sort
from datetime import date

from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    where = ((F("Status") == "Open") | (F("Status") == "Blocked")) & (
        F("Due Date") < date(2026, 5, 1)
    )
    for task in tasks.query(where=where, select=["Title", "Due Date"], order_by="-Due Date"):
        print(task["Due Date"], task["Title"])
