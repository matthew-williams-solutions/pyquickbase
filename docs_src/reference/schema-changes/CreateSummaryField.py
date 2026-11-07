from pyquickbase import (
    CreateRelationship,
    CreateSummaryField,
)

plan = await app.preview_schema_changes(
    [
        CreateRelationship(
            child_table="Job Tasks",
            parent_table="Job Projects",
            foreign_key="Project",
        ),
        CreateSummaryField(
            child_table="Job Tasks",
            foreign_key="Project",
            label="Total Hours",
            child_field="Hours",
        ),
    ]
)
print(plan.changes[1].summary)
