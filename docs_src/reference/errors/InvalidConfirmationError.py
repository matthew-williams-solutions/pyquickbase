from pyquickbase import InvalidConfirmationError

update = await tasks.operation("update", require_preview=True)
try:
    await update.run(
        {"records": [{"Record ID#": 1, "Status": "Done"}]}
    )
except InvalidConfirmationError:
    print("Preview it first")
