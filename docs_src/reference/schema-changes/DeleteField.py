from pyquickbase import DeleteField

plan = await app.plan_schema(
    [DeleteField(table="Job Tasks", field="Hours")]
)
print(plan.changes[0].warnings[0])
