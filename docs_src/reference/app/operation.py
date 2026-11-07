plan = await app.operation(
    "preview_schema_changes", max_changes=5
)
print(plan.schema["properties"]["changes"]["maxItems"])
