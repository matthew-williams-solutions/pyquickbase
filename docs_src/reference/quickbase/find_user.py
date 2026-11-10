me = await qb.current_user()
found = await qb.find_user(me.email)
print(found.id == me.id)
