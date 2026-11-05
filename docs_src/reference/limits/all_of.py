from pyquickbase import (
    Allow,
    AsyncQuickbase,
    ReadOnlyError,
    TableRules,
    all_of,
)


def small_writes(request):
    return (request.row_count or 0) <= 1


rules = TableRules({"bqtasks123": Allow(insert=True)})
policy = all_of(rules, small_writes)
async with AsyncQuickbase.from_env(write_policy=policy) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    print(
        (await tasks.insert([{"Title": "One"}])).created_ids
    )
    try:
        await tasks.insert([{"Title": "A"}, {"Title": "B"}])
    except ReadOnlyError:
        print("Two rows at once was refused")
