typed = app.typed(JobTasks)
field = JobTasks.fields.hours
print(field.id, field.desc)
async for task in typed.query(
    where=field >= 8, order_by=field.desc
):
    print(task.title)
