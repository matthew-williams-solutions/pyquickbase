# Preview a schema change before making it
from pyquickbase import CreateField, DeleteField, Quickbase, UpdateField

with Quickbase(realm="demo", token="demo") as qb:
    app = qb.app("bq1234567")
    plan = app.preview_schema_changes(
        [
            CreateField(table="Tasks", label="Reviewed", type="checkbox"),
            UpdateField(table="Tasks", field="Due Date", required=True),
            DeleteField(table="Tasks", field="Hours"),
        ]
    )
    for change in plan.changes:
        print(f"[{change.status}, {change.risk}] {change.description}")
        for note in change.warnings + change.blockers:
            print("   ", note)

    labels = [field.label for field in app.table("Tasks").fields()]
    print("Made yet:", "Reviewed" in labels)
