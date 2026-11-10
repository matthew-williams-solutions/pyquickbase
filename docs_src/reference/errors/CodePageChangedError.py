from pyquickbase import CodePageChangedError

page = app.code_page("pyquickbase-live-test.html")
try:
    await page.write("x", expected_sha256="0" * 64)
except CodePageChangedError as error:
    print(type(error).__name__)
