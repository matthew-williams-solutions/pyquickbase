from pyquickbase import AuthenticationError

try:
    await qb.app("bzzzzzzzz").info()
except AuthenticationError as error:
    print(error.status_code)
    print(error.message)
