relationship = await tasks.ensure_relationship(
    projects, foreign_key="Project"
)
field = await tasks.ensure_count_field(
    relationship, "Task Count"
)
print(field.label, field.summary_function)
