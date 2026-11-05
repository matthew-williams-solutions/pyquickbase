result = await tasks.update(
    [
        {"Record ID#": 1, "Status": "Done"},
        {"Record ID#": 2, "Status": "Open"},  # Already Open
    ]
)
print(result.updated_ids, result.unchanged_ids)
