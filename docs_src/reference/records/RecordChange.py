preview = await tasks.preview_update(
    [{"Record ID#": 1, "Status": "Done"}]
)
change = preview.changes[0]
print(change.record_id, sorted(change.fields))
