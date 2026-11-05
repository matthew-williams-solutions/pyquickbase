relationship = await tasks.ensure_relationship(
    projects, foreign_key_label="Project"
)
again = await tasks.ensure_relationship(
    projects, foreign_key_label="Project"
)
print(relationship.id == again.id)
