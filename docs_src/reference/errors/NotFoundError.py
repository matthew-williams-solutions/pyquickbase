from pyquickbase import NotFoundError

try:
    await tasks.download_file(999, "Attachment")
except NotFoundError as error:
    print(error.status_code)
