# Explore an app, its tables, and fields
from pyquickbase import Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    app = qb.app("bq1234567")
    print(app.info().name)
    print([table.name for table in app.tables()])

    tasks = app.table("Tasks")
    for field in tasks.fields():
        print(field.id, field.label, field.type)

    for link in tasks.relationships():
        print("Linked from", link.parent_table_id, "by field", link.foreign_key_field_id)
