for field in await tasks.fields():
    if field.label == "Status":
        print(field.id, field.type, field.mode)
        print(sorted(field.choices), field.required)
