# Watch computed fields update
from datetime import date

from pyquickbase import F, Quickbase, WriteError

with Quickbase(realm="demo", token="demo") as qb:
    app = qb.app("bq1234567")
    tasks = app.table("Tasks")

    for field in tasks.fields():
        if field.mode:
            print(f"{field.label} is a {field.mode} field")

    def status() -> str:
        task = next(
            iter(tasks.query(where=F("Record ID#") == 8, select=["Hours Logged", "Over Estimate"]))
        )
        return f"{task['Hours Logged']} hours logged, over estimate: {task['Over Estimate']}"

    print("\nBefore:", status())
    entry = {"Task": 8, "Date": date(2026, 4, 16), "Hours": 7.0, "Billable": True}
    app.table("Time Entries").insert([entry])
    print("After:", status())

    print("\nComputed fields can't be written:")
    try:
        app.table("Time Entries").insert([{"Hours": 1.0, "Task Title": "Not allowed"}])
    except WriteError as error:
        print(*error.result.failed_rows[0].messages)
