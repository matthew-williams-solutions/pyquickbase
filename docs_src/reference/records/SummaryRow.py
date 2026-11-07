rows = await tasks.summarize(
    "Status", sum="Hours", max="Hours"
)
for row in rows:
    print(row.group["Status"], row.count)
    print(row.sum["Hours"], row.max["Hours"])
