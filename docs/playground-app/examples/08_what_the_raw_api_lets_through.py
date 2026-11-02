# What the raw API lets through
from pyquickbase import F, Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    # Sent directly, a lowercase "ex" acts like "contains", with no error.
    response = qb.request(
        "POST", "/records/query", json={"from": "bqtasks", "where": "{6.ex.'review'}"}
    )
    print("Raw request matched", response["metadata"]["totalRecords"], "records")

    # The SDK writes the correct operator, which matches the exact title only.
    print("pyquickbase matched", tasks.count(F("Title") == "review"), "records")
