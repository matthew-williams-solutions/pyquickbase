from pyquickbase import F

task = await tasks.first(
    where=F("Status") == "Open", order_by="Due Date"
)
print(task["Title"] if task else "Nothing is open")
