from pyquickbase import ConfirmationError

update = await tasks.operation("update", require_preview=True)
try:
    await update.run(
        {"records": [{"Record ID#": 1, "Status": "Done"}]}
    )
except ConfirmationError:
    print("Preview it first")
