# Use the async client
from pyquickbase import AsyncQuickbase, F

async with AsyncQuickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    print(await tasks.count(F("Status") == "Blocked"), "blocked tasks")
    async for task in tasks.query(where=F("Status") == "Blocked", select=["Title"]):
        print(task["Title"])
