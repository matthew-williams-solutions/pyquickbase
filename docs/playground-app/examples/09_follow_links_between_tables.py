# Follow links between tables
from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    app = qb.app("bq1234567")
    projects = app.table("Projects")
    tasks = app.table("Tasks")

    columns = ["Name", "Budget", "Task Count", "Total Hours"]
    for project in projects.query(where=F("Status") == "Active", select=columns, order_by="Name"):
        print(
            f"{project['Name']}: {project['Task Count']:.0f} tasks, {project['Total Hours']} hours"
        )

        blocked = (F("Related Project") == project.id) & (F("Status") == "Blocked")
        for task in tasks.query(where=blocked, select=["Title", "Assignee"]):
            print(f"  Blocked: {task['Title']} ({task['Assignee'].name})")
