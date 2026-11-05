from pyquickbase import QuickbaseError

try:
    await tasks.get(1, select=["No Such Field"])
except QuickbaseError as error:
    print(type(error).__name__)
