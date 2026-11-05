from pyquickbase import (
    Allow,
    AsyncQuickbase,
    ReadOnlyError,
    TableRules,
)

status_only = Allow(update=[7])
rules = TableRules({"bqtasks123": status_only})
async with AsyncQuickbase.from_env(write_policy=rules) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    done = {"Record ID#": 1, "Status": "Done"}
    print((await tasks.update([done])).updated_ids)
    try:
        await tasks.update(
            [{"Record ID#": 1, "Title": "New"}]
        )
    except ReadOnlyError:
        print("Changing the title was refused")
