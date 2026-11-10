relationship = await tasks.ensure_relationship(
    projects, foreign_key="Project"
)
print(relationship.foreign_key_field_id)
print(relationship.lookup_field_ids, relationship.cross_app)
