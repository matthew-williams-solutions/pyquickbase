for field in await tasks.fields():
    if field.label in ("Title", "Status"):
        print(field.id, field.label, field.type)
