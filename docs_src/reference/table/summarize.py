rows = await tasks.summarize("Status", sum="Hours")
for row in rows:
    print(row.group["Status"], row.count, row.sum["Hours"])
