from pyquickbase import AsyncQuickbase, RetryPolicy

patient = RetryPolicy(max_retries=6, max_delay=60)
async with AsyncQuickbase.from_env(retry=patient) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    print(await tasks.count())
