from pyquickbase import (
    AsyncQuickbase,
    Policy,
    allow,
    deny,
    fields,
    tables,
)

policy = Policy(
    classes={"internal": [("Job Tasks", "Code")]},
    rules=[
        allow.read(tables.all(), max_paging_depth=50),
        allow.update("Job Tasks", ["Status"]),
        deny.all(fields.classed("internal")),
    ],
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
