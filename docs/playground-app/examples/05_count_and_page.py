# Count records and fetch one page
from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    print(tasks.count(F("Notes") == None), "tasks have no notes")

    page = tasks.query_page(where=F("Active") == True, select=["Title"], top=3, skip=0)
    print(len(page.records), "of", page.total, "| more:", page.has_more, "| next:", page.next_skip)
    for task in page.records:
        print(task["Title"])
