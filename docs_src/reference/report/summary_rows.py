report = tasks.report("Hours by Status")
for row in await report.summary_rows():
    print(row["Status"], row["Hours (tot)"])
