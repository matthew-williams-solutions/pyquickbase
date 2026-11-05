for report in await tasks.reports():
    print(report.name, report.type)
