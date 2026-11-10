member = (await app.members())[0]
print(member.kind, member.last_access is not None)
