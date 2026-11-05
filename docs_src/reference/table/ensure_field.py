field = await tasks.ensure_field("Priority", "text")
again = await tasks.ensure_field("Priority", "text")
print(field.id == again.id)
