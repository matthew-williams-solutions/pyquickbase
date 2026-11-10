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
    for failure in error.result.failed_rows:
        print(
            failure.row_index,
            failure.reason,
            failure.field_id,
        )
