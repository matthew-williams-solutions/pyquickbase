preview = await tasks.preview_update(
    [{"Record ID#": 1, "Status": "Done"}]
)
status = preview.changes[0].fields["Status"]
print(status.before, status.after)
