records = tasks.query(select=["Title"], limit=2)
async for task in records:
    print(task.id, task["Title"])
