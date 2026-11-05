from datetime import date

rows = [
    {
        "Title": "Install windows",
        "Due Date": date(2027, 3, 1),
    },
    {"Title": "Paint walls", "Status": "Open"},
]
result = await tasks.insert(rows)
print(result.created_ids)
