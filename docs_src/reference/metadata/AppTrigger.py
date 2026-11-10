for event in await tasks.triggers():
    if event.active and event.runs_on_changes:
        print(event.type, event.name or event.url)
