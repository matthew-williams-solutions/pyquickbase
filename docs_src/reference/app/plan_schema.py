from pyquickbase import CreateField

change = CreateField(
    table="Job Tasks", label="Crew", type="text"
)
plan = await app.plan_schema([change])
for planned in plan.changes:
    print(planned.status, planned.summary)
