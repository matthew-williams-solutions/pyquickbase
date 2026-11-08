from pyquickbase import (
    AsyncQuickbase,
    Policy,
    WriteRequest,
    allow,
    fields,
)


class Log:
    def decide(self, request):
        if isinstance(request, WriteRequest):
            print(request.action, request.row_count)
            print(sorted(request.field_ids))
        return None


policy = Policy(
    rules=[
        allow.read("Job Tasks"),
        allow.insert("Job Tasks", fields.all()),
        Log(),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    await tasks.insert([{"Title": "Paint walls"}])
