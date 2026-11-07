from pyquickbase import (
    AsyncQuickbase,
    Policy,
    QuickbaseError,
    tool_error,
)

async with AsyncQuickbase.from_env(
    policy=Policy.read_only()
) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    try:
        await tasks.insert([{"Title": "Paint walls"}])
    except QuickbaseError as error:
        found = tool_error(error)["error"]
        print(found["code"], found["changed"])
        print(found["hint"])
