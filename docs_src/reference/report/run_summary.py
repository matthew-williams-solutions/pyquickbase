report = tasks.report("Hours by Status")
for row in await report.run_summary():
    print(row["Status"], row["Hours (tot)"])
