from pyquickbase import (
    AsyncQuickbase,
    ReadAccess,
    ReadRefusedError,
    ReadRules,
)

rules = ReadRules({"bqtasks123": ReadAccess(fields=[6])})
async with AsyncQuickbase.from_env(read_policy=rules) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    try:
        await tasks.run_formula("1 + 1")
    except ReadRefusedError:
        print("Formulas are refused")
