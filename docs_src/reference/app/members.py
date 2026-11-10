members = await app.members()
print(all(item.roles for item in members))
