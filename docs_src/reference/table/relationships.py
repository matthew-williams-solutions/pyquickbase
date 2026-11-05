for relationship in await tasks.relationships():
    print(relationship.foreign_key_field_id)
