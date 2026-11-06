relationship = (await tasks.relationships())[0]
await tasks.delete_relationship(relationship)
print(await tasks.relationships())
