from pyquickbase import (
    AsyncQuickbase,
    F,
    Policy,
    PolicyRefusedError,
    allow,
)

policy = Policy(
    rules=[
        allow.read("Job Tasks"),
        allow.insert("Job Tasks", ["Title", "Status"]),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    result = await tasks.insert([{"Title": "Paint walls"}])
    print(result.created_ids)
    try:
        await tasks.delete(F("Title") == "Paint walls")
    except PolicyRefusedError as error:
        print(error.decision.reason)
        print(error.decision.hint)
