# Block query injection
from pyquickbase import F, QueryError, Quickbase

# A page shows visitors their project's tasks, using the project from its URL.
# This visitor edited the URL.
project = "1'}OR{3.GT.'0"

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    print(f"Project 1 has {tasks.count(F('Related Project') == 1)} tasks")

    body = {"from": "bqtasks", "where": "{12.EX.'" + project + "'}"}
    records = qb.request("POST", "/records/query", json=body)["data"]
    print(f"Raw API: no error, and it returned {len(records)} tasks")

    try:
        tasks.count(F("Related Project") == project)
    except QueryError as error:
        print(f"pyquickbase: QueryError. {error}")
