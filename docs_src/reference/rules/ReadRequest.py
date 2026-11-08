from pyquickbase import (
    AsyncQuickbase,
    Policy,
    ReadRequest,
    allow,
)


class Log:
    def decide(self, request):
        if isinstance(request, ReadRequest):
            print(
                request.action,
                sorted(request.field_ids or []),
            )
        return None


policy = Policy(rules=[allow.read("Job Tasks"), Log()])
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    await tasks.first(select=["Title"])
