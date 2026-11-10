found = await app.code_page(
    "pyquickbase-live-test.html"
).read()
print(found.name, len(found.sha256))
