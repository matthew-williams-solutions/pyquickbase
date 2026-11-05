from pyquickbase import AsyncQuickbase


def log_and_allow(request):
    print(request.action, sorted(request.field_ids or []))
    return True


async with AsyncQuickbase.from_env(
    read_policy=log_and_allow
) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    await tasks.first(select=["Title"])
