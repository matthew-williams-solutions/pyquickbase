from pyquickbase import InvalidQueryError

try:
    await tasks.count("{7.ex.'Open'}")
except InvalidQueryError:
    print("A lowercase operator was refused")
