from pyquickbase import CreateField

change = CreateField(
    table="Job Tasks", label="Crew", type="text"
)
plan = await app.plan_schema([change])
result = await app.apply_schema(plan)
print(len(result.applied))
again = await app.plan_schema([change])
print(again.changes[0].status)
