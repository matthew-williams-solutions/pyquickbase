from pyquickbase import AsyncQuickbase, Policy, allow, deny

policy = Policy(
    rules=[
        allow.read("Job Tasks"),
        deny.read("Job Tasks", ["Hours"]),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    print(
        [
            item.label
            for item in await tasks.fields()
            if item.id > 5
        ]
    )
