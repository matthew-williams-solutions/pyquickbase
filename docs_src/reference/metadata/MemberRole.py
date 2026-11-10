first = (await app.member_roles())[0]
print(first.role.name != "", first.assigned_through)
