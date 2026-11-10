from pyquickbase import AsyncQuickbase, Policy, allow

policy = Policy(rules=[allow.update("Job Tasks", ["Status"])])
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
decision = bound.allows("update", "Job Tasks", field="Status")
print(decision.allowed, decision.rule)
print(
    bound.allows(
        "update", "Job Tasks", field="Hours"
    ).message()
)
