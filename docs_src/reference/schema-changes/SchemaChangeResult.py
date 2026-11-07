from pyquickbase import CreateField, UpdateField

plan = await app.preview_schema_changes(
    [
        CreateField(
            table="Job Tasks", label="Crew", type="text"
        ),
        UpdateField(
            table="Job Tasks", field="Code", unique=True
        ),
    ]
)
result = await app.apply_schema_changes(plan)
print(len(result.applied), len(result.already_done))
