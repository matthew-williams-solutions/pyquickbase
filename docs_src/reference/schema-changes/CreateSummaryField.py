from pyquickbase import (
    CreateRelationship,
    CreateSummaryField,
)

plan = await app.plan_schema(
    [
        CreateRelationship(
            child="Job Tasks",
            parent="Job Projects",
            foreign_key="Project",
        ),
        CreateSummaryField(
            child="Job Tasks",
            foreign_key="Project",
            label="Total Hours",
            child_field="Hours",
        ),
    ]
)
print(plan.changes[1].summary)
