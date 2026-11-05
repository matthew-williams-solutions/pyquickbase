result = await tasks.upsert(
    [JobTasks.new(code="T-9", title="Inspect site")],
    merge_on=JobTasks.fields.code,
)
print(result.created_ids)
