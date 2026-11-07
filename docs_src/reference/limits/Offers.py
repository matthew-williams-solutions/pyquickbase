from pyquickbase import AsyncQuickbase, Policy, allow

policy = Policy(
    rules=[
        allow.read("Job Tasks", ["Title", "Status"]),
        allow.update("Job Tasks", ["Status"]),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
offers = bound.offers("Job Tasks")
print([item.name for item in offers.readable])
print([item.name for item in offers.updatable])
print(offers.can_delete, offers.max_paging_depth)
