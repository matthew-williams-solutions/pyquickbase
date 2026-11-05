rows = await tasks.summarize(
    "Status", total="Hours", maximum="Hours"
)
for row in rows:
    print(row.group["Status"], row.count)
    print(row.total["Hours"], row.maximum["Hours"])
