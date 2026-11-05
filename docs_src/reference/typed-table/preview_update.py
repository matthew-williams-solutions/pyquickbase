preview = await tasks.preview_update(
    [JobTasks.change(1, status="Done")]
)
print(len(preview.changes), preview.unchanged_ids)
