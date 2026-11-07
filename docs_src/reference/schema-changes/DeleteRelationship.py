from pyquickbase import DeleteRelationship

change = DeleteRelationship(
    child_table="Job Tasks", foreign_key="Project"
)
plan = await app.preview_schema_changes([change])
print(plan.changes[0].summary)
