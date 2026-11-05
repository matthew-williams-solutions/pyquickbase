from pyquickbase import AsyncQuickbase, RequestRefusedError

async with AsyncQuickbase.from_env(read_only=True) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    try:
        await tasks.delete("{3.EX.'1'}")
    except RequestRefusedError as error:
        print(type(error).__name__)
