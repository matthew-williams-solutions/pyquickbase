field = await tasks.create_field(
    "Priority",
    "text-multiple-choice",
    choices=["Low", "Medium", "High"],
)
print(field.label, field.type)
