from pyquickbase import DeleteTable

plan = await app.plan_schema([DeleteTable(table="Job Tasks")])
change = plan.changes[0]
print(change.risk, change.affected_records)
