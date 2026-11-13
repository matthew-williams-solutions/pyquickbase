# Limit what an agent can do
from pyquickbase import F, Policy, PolicyRefusedError, Quickbase, UnknownFieldError, allow, deny

policy = Policy(
    rules=[
        allow.read("Tasks", max_paging_depth=50),
        allow.update("Tasks", ["Status"]),
        deny.read("Tasks", ["Hours"]),
    ]
)

with Quickbase(realm="demo", token="demo") as setup:
    bound = policy.bind(setup.app("bq1234567"))

with Quickbase(realm="demo", token="demo", policy=bound) as agent:
    tasks = agent.app("bq1234567").table("Tasks")
    print("Fields it sees:", [field.label for field in tasks.fields()][:6], "...")

    try:
        tasks.count(F("Hours") > 10)
    except UnknownFieldError as error:
        print("Hidden field:", error)

    try:
        tasks.update([{"Record ID#": 1, "Title": "Renamed"}])
    except PolicyRefusedError as error:
        print("Refused:", error)

    result = tasks.update([{"Record ID#": 1, "Status": "Done"}])
    print("Allowed:", result.updated_ids)
