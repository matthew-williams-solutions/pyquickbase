typed = app.typed(JobTasks)
page = await typed.query_page(limit=2)
print(page.total, page.has_more, page.next_skip)
print(page.records[0].title)
