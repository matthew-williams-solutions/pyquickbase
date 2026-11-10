page = await tasks.query_page(limit=2)
print(len(page.records), page.total, page.skip)
print(page.has_more, page.next_skip)
