task = await tasks.get(
    1, select=["Title", "Status", "Due Date"]
)
print(task.id, task["Title"], task[7])
print(task["Due Date"])
print(sorted(label for label in task))
