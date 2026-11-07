from pyquickbase import CreateRelationship

plan = await app.preview_schema_changes(
    [
        CreateRelationship(
            child_table="Job Tasks",
            parent_table="Job Projects",
            foreign_key="Project",
        )
    ]
)
print(plan.changes[0].summary)
