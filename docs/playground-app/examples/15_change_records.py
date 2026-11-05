# Change records and preview the change
from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")
    blocked = [task.id for task in tasks.query(where=F("Status") == "Blocked")]

    preview = tasks.preview_update([{"Record ID#": rid, "Status": "Open"} for rid in blocked])
    for change in preview.changes:
        for label, field in change.fields.items():
            print(f"Task {change.record_id}: {label} {field.before} -> {field.after}")

    result = tasks.update([{"Record ID#": rid, "Status": "Open"} for rid in blocked])
    print("Updated:", result.updated_ids)
    print("Still blocked:", tasks.count(F("Status") == "Blocked"))
