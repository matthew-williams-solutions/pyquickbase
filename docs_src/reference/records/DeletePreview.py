from pyquickbase import F

preview = await tasks.preview_delete(F("Status") == "Done")
print(preview.total, preview.triggers)
