page = await tasks.query_page(select=["Title"], top=2)
print(page.total, page.has_more, page.next_skip)
for task in page.records:
    print(task.id, task["Title"])
