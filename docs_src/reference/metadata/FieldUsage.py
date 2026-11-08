[usage] = await tasks.field_usage("Title")
print(usage.field_id, usage.label)
print(usage.counts["default_reports"], usage.total > 0)
print("report" in usage.describe())
