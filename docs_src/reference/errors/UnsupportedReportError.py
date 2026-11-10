from pyquickbase import UnsupportedReportError

try:
    await tasks.report("List All").run_summary()
except UnsupportedReportError:
    print("That's a table report, so use run()")
