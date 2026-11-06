from pyquickbase import F

where = (F("Status") == "Open") & F("Title").contains("slab")
print(await tasks.compile_where(where))
