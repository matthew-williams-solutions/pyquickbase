from pyquickbase import (
    AsyncQuickbase,
    Decision,
    Policy,
    PolicyRefusedError,
    WriteRequest,
    allow,
    fields,
)


class OneRowAtATime:
    def decide(self, request):
        if (
            isinstance(request, WriteRequest)
            and (request.row_count or 0) > 1
        ):
            return Decision(
                allowed=False,
                verb="insert",
                reason="Write one row at a time",
            )
        return None


policy = Policy(
    rules=[
        allow.read("Job Tasks"),
        allow.insert("Job Tasks", fields.all()),
        OneRowAtATime(),
    ]
)
async with AsyncQuickbase.from_env() as setup:
    bound = await policy.bind(setup.app("bq1234567"))
async with AsyncQuickbase.from_env(policy=bound) as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
    print(
        (await tasks.insert([{"Title": "One"}])).created_ids
    )
    try:
        await tasks.insert([{"Title": "A"}, {"Title": "B"}])
    except PolicyRefusedError as error:
        print(error.decision.reason)
