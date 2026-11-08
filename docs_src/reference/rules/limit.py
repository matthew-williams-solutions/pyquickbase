from pyquickbase import (
    AsyncQuickbase,
    F,
    Policy,
    allow,
    limit,
)

policy = Policy(
    rules=[
        allow.read("Job Tasks"),
        limit.rows("Job Tasks", F("Status") == "Open"),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    print(await tasks.count())
    print(await tasks.count(F("Hours") > 7))
