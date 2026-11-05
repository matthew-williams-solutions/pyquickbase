from pyquickbase import F

print(await tasks.delete(F("Status") == "Done"))
