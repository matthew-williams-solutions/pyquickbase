result = await tasks.update(
    [JobTasks.change(1, status="Done")]
)
print(result.updated_ids)
