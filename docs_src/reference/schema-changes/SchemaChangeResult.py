from pyquickbase import CreateField, UpdateField

plan = await app.plan_schema(
    [
        CreateField(
            table="Job Tasks", label="Crew", type="text"
        ),
        UpdateField(
            table="Job Tasks", field="Code", unique=True
        ),
    ]
)
result = await app.apply_schema(plan)
print(len(result.applied), len(result.unchanged))
