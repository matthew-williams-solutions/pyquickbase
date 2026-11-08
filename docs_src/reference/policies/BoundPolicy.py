from pyquickbase import (
    AsyncQuickbase,
    BoundPolicy,
    Policy,
    allow,
)

policy = Policy(
    rules=[allow.read("Job Tasks", ["Title", "Status"])]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
print(bound.explain("read", "Job Tasks", field="Hours"))
saved = bound.to_json()
print(BoundPolicy.from_json(saved) == bound)
