relationship = await tasks.ensure_relationship(
    projects, foreign_key_label="Project"
)
field = await tasks.ensure_lookup_field(
    relationship, "Name", label="Project Name"
)
print(field.label)
