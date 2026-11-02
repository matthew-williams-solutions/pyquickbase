# Run a saved report
from pyquickbase import Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    print([(report.id, report.name, report.type) for report in tasks.reports()])

    for row in tasks.report("Open tasks").run(limit=3):
        print(row["Due Date"], row["Title"])
