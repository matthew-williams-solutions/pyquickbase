from pyquickbase import AsyncQuickbase, WriteRefusedError

async with AsyncQuickbase.from_env(read_only=True) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    try:
        await tasks.insert([{"Title": "Paint walls"}])
    except WriteRefusedError:
        print("It wasn't sent")
