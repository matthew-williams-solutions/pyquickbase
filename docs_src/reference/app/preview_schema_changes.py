from pyquickbase import CreateField

change = CreateField(
    table="Job Tasks", label="Crew", type="text"
)
plan = await app.preview_schema_changes([change])
for planned in plan.changes:
    print(planned.status, planned.description)
