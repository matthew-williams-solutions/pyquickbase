plan = await app.operation("plan_schema", max_changes=5)
print(plan.schema["properties"]["changes"]["maxItems"])
