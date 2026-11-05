from pyquickbase import AsyncQuickbase

async with AsyncQuickbase.from_env(read_only=True) as qb:
    app = qb.app("bq1234567")
    print(app.id)
