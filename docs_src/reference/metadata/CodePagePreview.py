page = app.code_page("pyquickbase-live-test.html")
preview = await page.preview_write((await page.read()).text)
print(preview.unchanged)
