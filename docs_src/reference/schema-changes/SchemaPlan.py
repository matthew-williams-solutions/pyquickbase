from pyquickbase import CreateField

change = CreateField(
    table="Job Tasks", label="Code", type="date"
)
plan = await app.preview_schema_changes([change])
print(plan.has_changes)
print(plan.blockers[0])
