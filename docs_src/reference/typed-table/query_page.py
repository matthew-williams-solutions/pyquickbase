page = await tasks.query_page(limit=2)
print(page.total, page.has_more, page.next_skip)
print([task.title for task in page.records])
