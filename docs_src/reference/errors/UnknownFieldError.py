from pyquickbase import F, UnknownFieldError

try:
    await tasks.count(F("Statuss") == "Open")
except UnknownFieldError as error:
    print(error)
