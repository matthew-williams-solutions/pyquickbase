from pyquickbase import (
    Allow,
    AsyncQuickbase,
    ReadOnlyError,
    TableRules,
)

rules = TableRules({"bqtasks123": Allow(insert=True)})
async with AsyncQuickbase.from_env(write_policy=rules) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    result = await tasks.insert([{"Title": "Paint walls"}])
    print(result.created_ids)
    try:
        await tasks.delete("{3.EX.'1'}")
    except ReadOnlyError:
        print("Deleting was refused")
