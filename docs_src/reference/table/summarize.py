rows = await tasks.summarize("Status", total="Hours")
for row in rows:
    print(row.group["Status"], row.count, row.total["Hours"])
