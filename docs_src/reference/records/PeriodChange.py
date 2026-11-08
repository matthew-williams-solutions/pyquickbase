from pyquickbase import Period

rows = await tasks.summarize(
    Period("Due Date", "month"),
    sum="Hours",
    compare_periods=True,
)
for row in rows:
    if row.change:
        print(row.group["Due Date"], row.change.previous)
        print(row.change.count, row.change.values["sum"])
