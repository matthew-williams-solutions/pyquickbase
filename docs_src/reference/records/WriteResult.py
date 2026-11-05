result = await tasks.upsert(
    [
        {"Code": "T-1", "Status": "Open"},
        {"Code": "T-2", "Status": "Done"},
        {"Code": "T-9", "Title": "Inspect site"},
    ],
    merge_on="Code",
)
print(result.created_ids)
print(result.updated_ids)
print(result.unchanged_ids)
print(result.errors)
