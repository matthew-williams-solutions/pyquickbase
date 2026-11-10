from pyquickbase import SummaryTooLargeError

try:
    await tasks.summarize(
        "Status", sum="Hours", max_records_scanned=1
    )
except SummaryTooLargeError as error:
    print(error.matched > error.limit, error.limit)
