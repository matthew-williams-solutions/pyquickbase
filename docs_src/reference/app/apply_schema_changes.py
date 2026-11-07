from pyquickbase import CreateField

change = CreateField(
    table="Job Tasks", label="Crew", type="text"
)
plan = await app.preview_schema_changes([change])
result = await app.apply_schema_changes(plan)
print(len(result.applied))
again = await app.preview_schema_changes([change])
print(again.changes[0].status)
