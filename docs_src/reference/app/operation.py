plan = await app.operation(
    "preview_schema_changes", max_changes=5
)
print(plan.input_schema["properties"]["changes"]["maxItems"])
