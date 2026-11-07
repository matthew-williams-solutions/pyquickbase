from pyquickbase import CreateTable

plan = await app.plan_schema(
    [CreateTable(name="Job Sites", single_record_name="Site")]
)
print(plan.changes[0].summary)
