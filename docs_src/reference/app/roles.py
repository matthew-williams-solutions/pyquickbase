roles = await app.roles()
print(any(role.name == "Administrator" for role in roles))
