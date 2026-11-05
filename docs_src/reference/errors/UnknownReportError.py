from pyquickbase import UnknownReportError

try:
    await tasks.report("No Such Report").info()
except UnknownReportError:
    print("There's no such report")
