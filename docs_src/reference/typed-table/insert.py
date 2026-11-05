result = await tasks.insert(
    [JobTasks.new(title="Paint walls", status="Open")]
)
print(result.created_ids)
