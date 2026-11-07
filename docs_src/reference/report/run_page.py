report = tasks.report("List All")
page = await report.run_page(limit=2)
print(page.total, page.has_more, page.next_skip)
