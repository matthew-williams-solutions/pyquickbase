page = await tasks.query_page(top=2)
print(page.total, page.has_more, page.next_skip)
print([task.title for task in page.records])
