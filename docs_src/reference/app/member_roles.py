for item in await app.member_roles():
    print(item.role.access, item.assigned_through)
