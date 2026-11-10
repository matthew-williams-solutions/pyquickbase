from pyquickbase import WriteError

try:
    await tasks.insert([{"Title": "Repeat", "Code": "T-1"}])
except WriteError as error:
    print(error.result.failed_rows[0].reason)
