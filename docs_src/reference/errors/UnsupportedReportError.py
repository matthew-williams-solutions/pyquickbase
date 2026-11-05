from pyquickbase import UnsupportedReportError

try:
    await tasks.report("List All").summary_rows()
except UnsupportedReportError:
    print("That's a table report, so use run()")
