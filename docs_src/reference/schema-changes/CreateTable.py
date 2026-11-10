from pyquickbase import CreateTable

plan = await app.preview_schema_changes(
    [CreateTable(name="Job Sites", single_record_name="Site")]
)
print(plan.changes[0].description)
