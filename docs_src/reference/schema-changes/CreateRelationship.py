from pyquickbase import CreateRelationship

plan = await app.plan_schema(
    [
        CreateRelationship(
            child="Job Tasks",
            parent="Job Projects",
            foreign_key="Project",
        )
    ]
)
print(plan.changes[0].summary)
