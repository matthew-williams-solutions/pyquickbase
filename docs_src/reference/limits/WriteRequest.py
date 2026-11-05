from pyquickbase import AsyncQuickbase


def log_and_allow(request):
    print(request.action, request.row_count)
    print(sorted(request.field_ids))
    return True


async with AsyncQuickbase.from_env(
    write_policy=log_and_allow
) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    await tasks.insert([{"Title": "Paint walls"}])
