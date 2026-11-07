from pyquickbase import (
    AsyncQuickbase,
    Policy,
    PolicyRefusedError,
)

async with AsyncQuickbase.from_env(
    policy=Policy.read_only()
) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    try:
        await tasks.insert([{"Title": "Paint walls"}])
    except PolicyRefusedError as error:
        print(error.decision.verb, error.decision.layer)
        print(error.decision.reason)
