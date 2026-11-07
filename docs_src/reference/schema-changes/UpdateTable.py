from pyquickbase import UpdateTable

change = UpdateTable(table="Job Tasks", description="Work.")
plan = await app.preview_schema_changes([change])
print(plan.changes[0].settings["description"].after)
