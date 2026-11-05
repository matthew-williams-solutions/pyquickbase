preview = await tasks.preview_update(
    [
        {"Record ID#": 1, "Status": "Done"},
        {"Record ID#": 2, "Status": "Open"},
        {"Record ID#": 999, "Status": "Done"},
    ]
)
print(len(preview.changes))
print(preview.unchanged_ids, preview.missing_ids)
