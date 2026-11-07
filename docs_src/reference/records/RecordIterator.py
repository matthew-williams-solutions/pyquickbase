records = tasks.query(select=["Title"], max_total=2)
async for task in records:
    print(task.id, task["Title"])
