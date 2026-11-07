from pyquickbase import (
    CreateLookupField,
    CreateRelationship,
)

plan = await app.preview_schema_changes(
    [
        CreateRelationship(
            child_table="Job Tasks",
            parent_table="Job Projects",
            foreign_key="Project",
        ),
        CreateLookupField(
            child_table="Job Tasks",
            foreign_key="Project",
            parent_field="Name",
        ),
    ]
)
print(plan.changes[1].summary)
