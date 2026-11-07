from pyquickbase import WriteError

try:
    await tasks.insert([{"Title": "Repeat", "Code": "T-1"}])
except WriteError as error:
    print(error.result.errors[0].reason)
