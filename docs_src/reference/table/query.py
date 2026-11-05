from pyquickbase import F

open_tasks = tasks.query(
    where=F("Status") == "Open",
    select=["Title", "Hours"],
    order_by="-Hours",
)
async for task in open_tasks:
    print(task.id, task["Title"], task["Hours"])
