from myapp.tables import JobTasks

tasks = app.typed(JobTasks)
async for task in tasks.query(
    where=JobTasks.fields.status == "Open",
):
    print(task.id, task.title)
