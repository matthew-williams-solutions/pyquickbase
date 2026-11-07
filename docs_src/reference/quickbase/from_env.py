from pyquickbase import AsyncQuickbase, Policy

async with AsyncQuickbase.from_env(
    policy=Policy.read_only()
) as qb:
    app = qb.app("bq1234567")
    print(app.id)
