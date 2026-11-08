from pyquickbase import AsyncQuickbase, Policy, allow, tables

policy = Policy(
    rules=[
        allow.create_tables(),
        allow.read(tables.created()),
        allow.schema(tables.created(), "add_field"),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    sites = await qb.app("bq1234567").create_table(
        "Job Sites"
    )
    await sites.create_field("Address", "text")
    print(
        [
            item.label
            for item in await sites.fields()
            if item.id > 5
        ]
    )
    print(len(qb.policy.created), len(bound.created))
