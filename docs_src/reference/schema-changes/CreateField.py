from pyquickbase import CreateField

plan = await app.preview_schema_changes(
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
