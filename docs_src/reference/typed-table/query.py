async for task in tasks.query(
    where=JobTasks.fields.status == "Open",
    order_by=JobTasks.fields.hours.desc,
    max_total=1,
):
    print(task.id, task.title, task.hours)
