from pyquickbase import CreateField

plan = await app.plan_schema(
    [
        CreateField(
            table="Job Tasks",
            label="Cost",
            type="numeric",
            formula="[Hours] * 40",
        )
    ]
)
print(plan.changes[0].summary)
