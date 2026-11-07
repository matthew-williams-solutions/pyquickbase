from pyquickbase import DeleteTable

plan = await app.preview_schema_changes(
    [DeleteTable(table="Job Tasks")]
)
change = plan.changes[0]
print(change.risk, change.affected_records)
