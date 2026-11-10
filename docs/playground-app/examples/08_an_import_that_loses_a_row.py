# An import that loses a row
from pyquickbase import Quickbase, WriteError

rows = [
    {"Title": "Order chairs", "Status": "Open"},
    {"Title": "Book venue", "Status": "Finished"},  # Not a valid status
    {"Title": "Send invites", "Status": "Open"},
]

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    try:
        tasks.insert(rows)
    except WriteError as error:
        print("Saved:", error.result.created_ids)
        for failed in error.result.failed_rows:
            print("Failed:", rows[failed.row_index]["Title"], "-", *failed.messages)
