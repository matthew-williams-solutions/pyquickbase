for role in await app.roles():
    if role.name == "Administrator":
        print(role.name, role.access)
