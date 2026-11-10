page = app.code_page("pyquickbase-live-test.html")
result = await page.write((await page.read()).text)
print(result.created, result.page.id is not None)
