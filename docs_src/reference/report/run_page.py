report = tasks.report("List All")
page = await report.run_page(top=2)
print(page.total, page.has_more, page.next_skip)
