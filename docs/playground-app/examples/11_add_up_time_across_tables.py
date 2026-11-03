# Add up time across tables
from collections import Counter

from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    app = qb.app("bq1234567")

    billable = Counter()
    for entry in app.table("Time Entries").query(where=F("Billable") == True):
        billable[entry["Person"]["name"]] += entry["Hours"]
    for name, hours in billable.most_common():
        print(f"{name}: {hours} billable hours")

    print()
    over = F("Over Estimate") == True
    for task in app.table("Tasks").query(where=over, select=["Title", "Hours", "Hours Logged"]):
        print(f"Over estimate: {task['Title']} ({task['Hours Logged']} of {task['Hours']} hours)")
