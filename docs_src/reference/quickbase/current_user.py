me = await qb.current_user()
print(bool(me.id and me.email))
