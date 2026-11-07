relationship = await tasks.ensure_relationship(
    projects, foreign_key="Project"
)
field = await tasks.ensure_summary_field(
    relationship, "Hours", "Total Hours"
)
print(field.label)
