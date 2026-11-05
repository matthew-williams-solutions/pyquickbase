from pyquickbase import F

page = await tasks.preview_delete(
    F("Status") == "Done", select=["Title"]
)
print(page.total, [task["Title"] for task in page.records])
