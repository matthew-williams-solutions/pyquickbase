relationship = await tasks.ensure_relationship(
    projects, foreign_key="Project"
)
again = await tasks.ensure_relationship(
    projects, foreign_key="Project"
)
print(relationship.id == again.id)
