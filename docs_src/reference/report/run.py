report = tasks.report("List All")
titles = [record["Title"] async for record in report.run()]
print(sorted(titles))
