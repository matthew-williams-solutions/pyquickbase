task = await tasks.first(
    where=JobTasks.fields.status == "Done"
)
print(task.title if task else "Nothing is done")
