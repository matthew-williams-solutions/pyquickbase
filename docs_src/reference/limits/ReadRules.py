from pyquickbase import (
    AsyncQuickbase,
    ReadAccess,
    ReadRefusedError,
    ReadRules,
)

# Returns Title and Status, and filters on Status only.
access = ReadAccess(fields=[6, 7], filter=[7])
rules = ReadRules({"bqtasks123": access})
async with AsyncQuickbase.from_env(
    read_policy=rules, read_only=True
) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    open_tasks = tasks.query(
        where="{7.EX.'Open'}", select=["Title"], limit=5
    )
    async for task in open_tasks:
        print(dict(task))
    try:
        await tasks.count("{6.EX.'Pour slab'}")
    except ReadRefusedError:
        print("Filtering on Title was refused")
