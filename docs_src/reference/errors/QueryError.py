from pyquickbase import QueryError

try:
    await tasks.count("{7.ex.'Open'}")
except QueryError:
    print("A lowercase operator was refused")
