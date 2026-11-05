reports = await tasks.reports()
first = reports[0]
print(first.name, first.type, first.incomplete)
