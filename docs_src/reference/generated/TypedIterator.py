typed = app.typed(JobTasks)
async for task in typed.query(limit=2):
    print(task.title)
