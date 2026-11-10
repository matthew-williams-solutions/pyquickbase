member = (await app.members())[0]
print(member.kind, member.last_accessed_at is not None)
