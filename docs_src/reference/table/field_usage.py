for usage in await tasks.field_usage(["Title", "Code"]):
    print(usage.label, usage.counts["reports"])
