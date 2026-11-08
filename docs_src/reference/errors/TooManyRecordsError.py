from pyquickbase import TooManyRecordsError

try:
    await tasks.summarize(
        "Status", sum="Hours", max_records_scanned=1
    )
except TooManyRecordsError as error:
    print(error.matched > error.limit, error.limit)
