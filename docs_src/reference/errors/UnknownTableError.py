from pyquickbase import UnknownTableError

try:
    await app.table("Job Task").info()
except UnknownTableError:
    print("There's no such table")
