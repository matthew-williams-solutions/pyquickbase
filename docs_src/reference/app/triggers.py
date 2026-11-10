events = await app.triggers()
print([event.type for event in events if event.active])
