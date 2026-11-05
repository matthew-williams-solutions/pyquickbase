result = await tasks.upsert(
    [
        {"Code": "T-1", "Status": "Done"},
        {
            "Code": "T-9",
            "Title": "Inspect site",
            "Status": "Open",
        },
    ],
    merge_on="Code",
)
print(result.updated_ids, result.created_ids)
