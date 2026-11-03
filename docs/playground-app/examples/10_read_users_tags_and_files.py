# Read users, tags, and file attachments
from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    columns = ["Title", "Assignee", "Tags", "Attachment", "Project Name"]
    task = next(iter(tasks.query(where=F("Record ID#") == 1, select=columns)))

    print(task["Title"], "in", task["Project Name"])
    print("Assigned to:", task["Assignee"]["name"])  # A user is a dictionary
    print("Tags:", task["Tags"])  # A list of text

    for version in task["Attachment"]["versions"]:  # Each version names who uploaded it
        print(version["versionNumber"], version["fileName"], version["creator"]["email"])
