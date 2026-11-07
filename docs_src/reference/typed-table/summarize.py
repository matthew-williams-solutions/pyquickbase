rows = await tasks.summarize(
    JobTasks.fields.status, sum=JobTasks.fields.hours
)
for row in rows:
    print(row.group["Status"], row.sum["Hours"])
