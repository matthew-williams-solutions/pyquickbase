from pyquickbase import CreateField

change = CreateField(
    table="Job Tasks", label="Code", type="date"
)
plan = await app.plan_schema([change])
print(plan.has_changes)
print(plan.blockers[0])
