import io

from pyquickbase import FileTooLargeError

try:
    await tasks.rows_from_file(
        io.StringIO("Title\nOne\nTwo\n"), max_rows=1
    )
except FileTooLargeError as error:
    print(error.limit, error.unit)
