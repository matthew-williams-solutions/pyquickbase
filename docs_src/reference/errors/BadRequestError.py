from pyquickbase import BadRequestError

try:
    await app.create_table("Job Tasks")
except BadRequestError as error:
    print(error.status_code)
