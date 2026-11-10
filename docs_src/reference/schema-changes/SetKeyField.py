from pyquickbase import SetKeyField

plan = await app.preview_schema_changes(
    [SetKeyField(table="Job Tasks", field="Code")]
)
print(plan.changes[0].status)
