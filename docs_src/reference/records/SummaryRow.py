rows = await tasks.summarize(
    "Status", sum="Hours", median="Hours"
)
for row in rows:
    print(row.group["Status"], row.count)
    print(row.sum["Hours"], row.median["Hours"])
