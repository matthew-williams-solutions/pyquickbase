typed = app.typed(JobTasks)
task = await typed.get(1)
print(task.id, task.title, task.hours, task.due_date)
print(task)
