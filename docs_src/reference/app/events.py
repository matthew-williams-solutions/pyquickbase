events = await app.events()
print([event.type for event in events if event.active])
