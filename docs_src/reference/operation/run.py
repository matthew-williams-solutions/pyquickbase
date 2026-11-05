page = await search.run(
    {
        "where": {
            "field": "Status",
            "op": "==",
            "value": "Open",
        },
        "select": ["Title"],
        "limit": 5,
    }
)
print([task["Title"] for task in page.records])
