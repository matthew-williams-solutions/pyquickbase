from pyquickbase import WriteError

try:
    await tasks.insert(
        [
            {"Title": "New", "Code": "T-8"},
            {"Title": "Repeat", "Code": "T-1"},
        ]
    )
except WriteError as error:
    print(error.result.created_ids)
    for failure in error.result.errors:
        print(failure.index, failure.kind, failure.field_id)
