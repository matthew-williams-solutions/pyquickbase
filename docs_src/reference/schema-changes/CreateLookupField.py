from pyquickbase import (
    CreateLookupField,
    CreateRelationship,
)

plan = await app.plan_schema(
    [
        CreateRelationship(
            child="Job Tasks",
            parent="Job Projects",
            foreign_key="Project",
        ),
        CreateLookupField(
            child="Job Tasks",
            foreign_key="Project",
            parent_field="Name",
        ),
    ]
)
print(plan.changes[1].summary)
