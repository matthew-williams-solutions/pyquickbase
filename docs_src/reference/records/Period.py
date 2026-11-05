from pyquickbase import Period

rows = await tasks.summarize(Period("Due Date", "month"))
for row in rows:
    print(row.group["Due Date"], row.count)
