# Delete records, after checking what would go
from pyquickbase import F, InvalidQueryError, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    try:
        tasks.delete("{7.ex.'Done'}")  # A lowercase operator would mean "contains"
    except InvalidQueryError as error:
        print("Refused, so nothing was deleted:", type(error).__name__)

    done = F("Status") == "Done"
    preview = tasks.preview_delete(done, select=["Title"], limit=3)
    print(preview.total, "would go, such as", [task["Title"] for task in preview.records])

    print("Deleted:", tasks.delete(done))
    print("Left:", tasks.count())
