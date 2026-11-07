from pyquickbase import DeleteRelationship

change = DeleteRelationship(
    child="Job Tasks", foreign_key="Project"
)
plan = await app.plan_schema([change])
print(plan.changes[0].summary)
