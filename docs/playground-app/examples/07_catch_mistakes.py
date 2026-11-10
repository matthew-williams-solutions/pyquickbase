# Catch mistakes before they run
from pyquickbase import F, InvalidQueryError, Quickbase, UnknownFieldError, UnsupportedReportError

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    try:
        tasks.count(F("Staus") == "Open")
    except UnknownFieldError as error:
        print("Misspelled field:", error)

    try:
        tasks.count("{7.ex.'Open'}")
    except InvalidQueryError as error:
        print("Lowercase operator:", error)

    try:
        list(tasks.report("Hours by status").run())
    except UnsupportedReportError as error:
        print("Summary report:", error)
