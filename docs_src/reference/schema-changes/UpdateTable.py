from pyquickbase import UpdateTable

change = UpdateTable(table="Job Tasks", description="Work.")
plan = await app.plan_schema([change])
print(plan.changes[0].settings["description"].after)
