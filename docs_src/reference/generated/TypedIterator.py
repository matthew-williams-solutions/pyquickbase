typed = app.typed(JobTasks)
async for task in typed.query(max_total=2):
    print(task.title)
