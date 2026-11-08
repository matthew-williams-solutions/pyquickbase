for event in await tasks.events():
    if event.active and event.runs_on_changes:
        print(event.type, event.name or event.url)
