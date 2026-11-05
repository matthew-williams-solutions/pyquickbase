from pyquickbase import (
    AsyncQuickbase,
    ReadAccess,
    ReadRefusedError,
    ReadRules,
    UnknownFieldError,
)

access = ReadAccess(fields=[6, 7], max_records=2)
rules = ReadRules({"bqtasks123": access})
async with AsyncQuickbase.from_env(read_policy=rules) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    print([field.label for field in await tasks.fields()])
    try:
        await tasks.first(select=["Hours"])
    except UnknownFieldError:
        print("Hours is hidden")
    try:
        await tasks.query_page(select=["Title"], top=3)
    except ReadRefusedError:
        print("Three records is more than allowed")
