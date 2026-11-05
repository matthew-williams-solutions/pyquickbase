from pyquickbase import F

print(await tasks.count())
print(await tasks.count(F("Status") == "Open"))
