from pyquickbase import InvalidValueError

try:
    await tasks.insert([{"Record ID#": 1, "Title": "Oops"}])
except InvalidValueError:
    print("Nothing was written")
