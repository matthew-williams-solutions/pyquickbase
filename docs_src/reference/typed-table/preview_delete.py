page = await tasks.preview_delete(
    JobTasks.fields.status == "Done"
)
print(page.total, [task.title for task in page.records])
