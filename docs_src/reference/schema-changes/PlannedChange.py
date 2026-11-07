from pyquickbase import UpdateField

change = UpdateField(
    table="Job Tasks", field="Code", unique=True
)
plan = await app.preview_schema_changes([change])
planned = plan.changes[0]
print(planned.status, planned.risk)
