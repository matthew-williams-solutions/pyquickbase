relationship = await tasks.create_relationship(
    projects,
    foreign_key_label="Project",
    lookup_fields=["Name"],
)
print(relationship.foreign_key_field_id)
