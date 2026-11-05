rows = await tasks.summarize(
    JobTasks.fields.status, total=JobTasks.fields.hours
)
for row in rows:
    print(row.group["Status"], row.total["Hours"])
