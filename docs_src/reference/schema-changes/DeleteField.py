from pyquickbase import DeleteField

plan = await app.preview_schema_changes(
    [DeleteField(table="Job Tasks", field="Hours")]
)
print(plan.changes[0].warnings[0])
